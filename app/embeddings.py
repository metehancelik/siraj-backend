"""bge-m3 embedding istemcisi. İki erişim modu:

- mode="openai": OpenAI-uyumlu /embeddings ucu (LiteLLM üzerinden veya TEI'nin /v1'i).
  Mevcut LiteLLM gateway'inize bge-m3'ü kaydedip aynı anahtarla erişmek için bunu kullanın.
- mode="tei":    HF Text Embeddings Inference'ın yerel /embed ucu (doğrudan, en hızlı).

bge-m3 önek istemez. multilingual-e5-large'a geçilirse query/passage önekleri gerekir.
"""
import httpx

from .config import settings


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
    # index'e göre sırala (bazı sağlayıcılar sırayı garanti etmez)
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


async def _embed_tei(texts: list[str], client: httpx.AsyncClient) -> list[list[float]]:
    url = f"{settings.embedding_url.rstrip('/')}/embed"
    resp = await client.post(url, json={"inputs": texts, "truncate": True})
    resp.raise_for_status()
    return resp.json()


async def embed(texts: list[str], kind: str = "passage",
                client: httpx.AsyncClient | None = None) -> list[list[float]]:
    """kind: 'query' (arama sorgusu) veya 'passage' (indekslenecek metin)."""
    texts = _prefix(texts, kind)
    own = client is None
    client = client or httpx.AsyncClient(timeout=120)
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
