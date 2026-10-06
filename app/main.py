"""Siraj RAG backend - SSE-streaming /v1/chat for the mobile chat.

Mobile protocol (text/event-stream), each line `data: {json}`:
  {"type":"sources", "sources":[{n,source,label,title,url}]}   (once, before generation)
  {"type":"delta",   "text":"..."}                              (many times)
  {"type":"done",    "cited":[n, ...]}                          (source numbers the answer cites)
  {"type":"error",   "message":"..."}
"""
import datetime as dt
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await get_pool()
    await migrate()
    yield
    await close_pool()


app = FastAPI(title="Siraj RAG", lifespan=_lifespan)

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
    # The app's selected interface language ("tr" | "en"). The corpus is Turkish; in
    # English the model reads the Turkish sources and answers in English (see app/prompt.py).
    lang: str = "tr"


def _check_auth(authorization: str | None) -> None:
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Geçersiz veya eksik yetki anahtarı")


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

    # Skip retrieval entirely for greetings/thanks/small talk: vector search works by
    # nearest neighbour, so it returns the closest passages even for a message like
    # "merhaba". Skipping avoids showing unrelated sources and speeds up the reply.
    chitchat = is_chitchat(question)
    passages = []
    if not chitchat:
        try:
            passages = await retrieve(question, lang)
        except Exception as exc:  # retrieval/embedding failure
            yield _sse({"type": "error", "message": f"{_RETRIEVAL_ERROR[lang]}: {exc}"})
            return

    sources = [{
        "n": i, "source": p.source,
        "label": source_label(p.source, lang),
        "title": p.title, "url": p.url,
    } for i, p in enumerate(passages, start=1)]
    yield _sse({"type": "sources", "sources": sources})

    # Keep earlier turns; enrich the last user message with the sources (if any).
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

    # Source numbers the answer actually cites ([1], [2], ...).
    # If empty the answer is not grounded in a source (e.g. a refusal) -> the client
    # does not show the sources.
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
    """Verse, hadith and supplication of the day. Contract: DAILY.md.

    The date is the CLIENT's local calendar day and arrives in the path; the server does
    not use `now()`, because it cannot know the reader's time zone and the day must turn
    over at their midnight. With the date in the path the response is cacheable.

    With `lang=en`, hadith and supplication are picked only from records that have an
    English text; if the pool is too small that card returns null. Showing Turkish text to
    someone using the app in English is not an option.

    503 if it cannot be assembled: the app reads this as a cue to silently fall back to its
    local path, so no error reaches the user. 404 is not used - if a day has no entry the
    rotation takes over, there is no such thing as "no day".
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

    # Six hours: a date's content is normally fixed, but when a curator changes a day by
    # hand the change has to propagate the same day.
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=21600"})
