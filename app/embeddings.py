"""bge-m3 embedding client. Three access modes:

- mode="openai": OpenAI-compatible /embeddings endpoint (via LiteLLM or TEI's /v1).
  Use this to register bge-m3 on your existing LiteLLM gateway and reach it with the same key.
- mode="tei":    the native /embed endpoint of HF Text Embeddings Inference (direct, fastest).
- mode="mps":    local Apple Silicon GPU via sentence-transformers. Only for a one-off
  local ingest (e.g. writing to a remote Postgres over an SSH tunnel); never used in
  production. Same bge-m3 weights + same pooling config -> measured cosine similarity
  of 1.0 against TEI.

bge-m3 needs no prefix. Switching to multilingual-e5-large requires query/passage prefixes.
"""
import asyncio

import httpx

from .config import settings

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer(settings.embedding_local_model, device="mps")
    return _local_model


def _prefix(texts: list[str], kind: str) -> list[str]:
    if not settings.embedding_use_e5_prefix:
        return texts
    tag = "query: " if kind == "query" else "passage: "
    return [tag + t for t in texts]


async def _embed_openai(texts: list[str], client: httpx.AsyncClient) -> list[list[float]]:
    url = f"{settings.embedding_base_url.rstrip('/')}/embeddings"
    headers = {"Content-Type": "application/json"}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    payload = {"model": settings.embedding_model, "input": texts}
    resp = await client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()["data"]
    # sort by index (some providers do not guarantee order)
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


async def _embed_tei(texts: list[str], client: httpx.AsyncClient) -> list[list[float]]:
    url = f"{settings.embedding_url.rstrip('/')}/embed"
    resp = await client.post(url, json={"inputs": texts, "truncate": True})
    resp.raise_for_status()
    return resp.json()


def _encode_local(texts: list[str]) -> list[list[float]]:
    model = _get_local_model()
    # batch_size=1: mixing long texts (dia/fetva) in one batch causes huge padding
    # -> attention memory blows up (measured: 30GB+ OOM). One at a time is safe.
    vecs = model.encode(texts, batch_size=1, normalize_embeddings=False,
                        show_progress_bar=False)
    return vecs.tolist()


async def _embed_mps(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_encode_local, texts)


async def embed(texts: list[str], kind: str = "passage",
                client: httpx.AsyncClient | None = None) -> list[list[float]]:
    """kind: 'query' (search query) or 'passage' (text to be indexed)."""
    texts = _prefix(texts, kind)
    if settings.embedding_mode == "mps":
        return await _embed_mps(texts)
    own = client is None
    # trust_env=False: ignore broken *_PROXY variables injected into the environment
    # (the embeddings service is reached over the internal network, no proxy needed).
    client = client or httpx.AsyncClient(timeout=120, trust_env=False)
    try:
        if settings.embedding_mode == "tei":
            return await _embed_tei(texts, client)
        return await _embed_openai(texts, client)
    finally:
        if own:
            await client.aclose()


async def embed_one(text: str, kind: str = "query") -> list[float]:
    vecs = await embed([text], kind=kind)
    return vecs[0]
