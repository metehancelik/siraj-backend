"""Siraj RAG backend — mobil sohbet için SSE akışlı /v1/chat.

Mobil protokolü (text/event-stream), her satır `data: {json}`:
  {"type":"sources", "sources":[{n,source,label,title,url}]}   (bir kez, üretimden önce)
  {"type":"delta",   "text":"..."}                              (çok kez)
  {"type":"done"}
  {"type":"error",   "message":"..."}
"""
import datetime as dt
import json
import re
from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import settings
from .daily import DailyUnavailable, build_daily
from .db import close_pool, get_pool, migrate
from .intent import is_app_name_question, is_chitchat
from .llm import stream_completion
from .prompt import build_user_message, normalize_lang, source_label, system_prompt
from .retrieval import retrieve

app = FastAPI(title="Siraj RAG")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    # Uygulamanın seçili arayüz dili ("tr" | "en"). Korpüs Türkçe; İngilizce'de model
    # Türkçe kaynakları okuyup İngilizce cevap verir (bkz. app/prompt.py).
    lang: str = "tr"


def _check_auth(authorization: str | None) -> None:
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Geçersiz veya eksik yetki anahtarı")


@app.on_event("startup")
async def _startup() -> None:
    await get_pool()
    await migrate()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()


@app.get("/health")
async def health() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT count(*) FROM chunks")
    return {"status": "ok", "chunks": n}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


_EMPTY_QUESTION = {"tr": "Boş soru", "en": "Empty question"}
_RETRIEVAL_ERROR = {"tr": "Kaynak getirme hatası", "en": "Source retrieval error"}
_MODEL_ERROR = {"tr": "Model hatası", "en": "Model error"}


async def _chat_stream(messages: list[Message], lang: str) -> AsyncIterator[str]:
    lang = normalize_lang(lang)
    question = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if not question.strip():
        yield _sse({"type": "error", "message": _EMPTY_QUESTION[lang]})
        return

    # Selamlaşma/teşekkür/kısa sohbet mesajlarında retrieval'ı hiç çalıştırma: vektör araması
    # "en yakın komşu" mantığıyla çalıştığı için "merhaba" gibi mesajlarda bile en yakın
    # pasajları getirir. Bu, hem alakasız kaynak göstermeyi önler hem yanıtı hızlandırır.
    chitchat = is_chitchat(question)
    passages = []
    if not chitchat:
        try:
            passages = await retrieve(question, lang)
        except Exception as exc:  # retrieval/embedding hatası
            yield _sse({"type": "error", "message": f"{_RETRIEVAL_ERROR[lang]}: {exc}"})
            return

    sources = [{
        "n": i, "source": p.source,
        "label": source_label(p.source, lang),
        "title": p.title, "url": p.url,
    } for i, p in enumerate(passages, start=1)]
    yield _sse({"type": "sources", "sources": sources})

    # Geçmiş turları koru, son kullanıcı mesajını kaynaklarla (varsa) zenginleştir.
    history = [m for m in messages if m.role in ("user", "assistant")]
    llm_messages = [{"role": "system", "content": system_prompt(lang, chitchat)}]
    for m in history[:-1]:
        llm_messages.append({"role": m.role, "content": m.content})
    last_content = question if chitchat else build_user_message(
        question, passages, lang, app_name_question=is_app_name_question(question))
    llm_messages.append({"role": "user", "content": last_content})

    answer_parts: list[str] = []
    try:
        async for delta in stream_completion(llm_messages):
            answer_parts.append(delta)
            yield _sse({"type": "delta", "text": delta})
    except Exception as exc:
        yield _sse({"type": "error", "message": f"{_MODEL_ERROR[lang]}: {exc}"})
        return

    # Cevabın gerçekten atıf yaptığı kaynak numaraları ([1], [2], ...).
    # Boşsa cevap kaynağa dayanmıyordur (ör. reddetme) → istemci kaynakları göstermez.
    answer = "".join(answer_parts)
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)
                    if 1 <= int(n) <= len(sources)})
    yield _sse({"type": "done", "cited": cited})


@app.post("/v1/chat")
async def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    return StreamingResponse(
        _chat_stream(req.messages, req.lang),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/daily/{date}")
async def daily(date: str, lang: str = "tr", authorization: str | None = Header(default=None)):
    """Günün ayeti, hadisi ve duası. Sözleşme: DAILY.md.

    Tarih İSTEMCİNİN yerel takvim günüdür ve yolda gelir; sunucu `now()` kullanmaz, çünkü
    okuyucunun saat dilimini bilemez ve gün onun gece yarısında dönmelidir. Tarih yolda
    olduğu için cevap önbelleklenebilir.

    `lang=en` verildiğinde hadis ve dua yalnızca İngilizcesi olan kayıtlar arasından
    seçilir; havuz yetmiyorsa o kart null döner. Uygulamayı İngilizce kullanan kişiye
    Türkçe metin göstermek seçenek değil.

    Derlenemezse 503: uygulama bunu sessizce kendi yerel yoluna düşmek için okur, yani
    kullanıcıya hata gösterilmez. 404 kullanılmaz — kayıtlı gün yoksa rotasyon devreye
    girer, "gün yok" diye bir durum yoktur.
    """
    _check_auth(authorization)
    try:
        day = dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Tarih YYYY-AA-GG olmalı")

    try:
        payload = await build_daily(day, lang)
    except DailyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Altı saat: bir tarihin içeriği normalde sabittir, ama küratör bir günü elle
    # değiştirdiğinde bunun aynı gün yayılması gerekir.
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=21600"})
