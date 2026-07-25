"""Siraj RAG backend — mobil sohbet için SSE akışlı /v1/chat.

Mobil protokolü (text/event-stream), her satır `data: {json}`:
  {"type":"sources", "sources":[{n,source,label,title,url}]}   (bir kez, üretimden önce)
  {"type":"delta",   "text":"..."}                              (çok kez)
  {"type":"done"}
  {"type":"error",   "message":"..."}
"""
import json
import re
from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import settings
from .db import close_pool, get_pool, migrate
from .intent import is_chitchat
from .llm import stream_completion
from .prompt import CHITCHAT_SYSTEM_PROMPT, SOURCE_LABELS, SYSTEM_PROMPT, build_user_message
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


async def _chat_stream(messages: list[Message]) -> AsyncIterator[str]:
    question = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if not question.strip():
        yield _sse({"type": "error", "message": "Boş soru"})
        return

    # Selamlaşma/teşekkür/kısa sohbet mesajlarında retrieval'ı hiç çalıştırma: vektör araması
    # "en yakın komşu" mantığıyla çalıştığı için "merhaba" gibi mesajlarda bile en yakın
    # pasajları getirir. Bu, hem alakasız kaynak göstermeyi önler hem yanıtı hızlandırır.
    chitchat = is_chitchat(question)
    passages = []
    if not chitchat:
        try:
            passages = await retrieve(question)
        except Exception as exc:  # retrieval/embedding hatası
            yield _sse({"type": "error", "message": f"Kaynak getirme hatası: {exc}"})
            return

    sources = [{
        "n": i, "source": p.source,
        "label": SOURCE_LABELS.get(p.source, p.source),
        "title": p.title, "url": p.url,
    } for i, p in enumerate(passages, start=1)]
    yield _sse({"type": "sources", "sources": sources})

    # Geçmiş turları koru, son kullanıcı mesajını kaynaklarla (varsa) zenginleştir.
    history = [m for m in messages if m.role in ("user", "assistant")]
    llm_messages = [{"role": "system", "content": CHITCHAT_SYSTEM_PROMPT if chitchat else SYSTEM_PROMPT}]
    for m in history[:-1]:
        llm_messages.append({"role": m.role, "content": m.content})
    last_content = question if chitchat else build_user_message(question, passages)
    llm_messages.append({"role": "user", "content": last_content})

    answer_parts: list[str] = []
    try:
        async for delta in stream_completion(llm_messages):
            answer_parts.append(delta)
            yield _sse({"type": "delta", "text": delta})
    except Exception as exc:
        yield _sse({"type": "error", "message": f"Model hatası: {exc}"})
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
        _chat_stream(req.messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
