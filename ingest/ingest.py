#!/usr/bin/env python3
"""JSONL kaynaklarını bölümle, embed et, Postgres'e (pgvector) yükle.

Kullanım:
    python -m ingest.ingest --data-dir ../data                # tüm kaynaklar
    python -m ingest.ingest --data-dir ../data --source fetva # tek kaynak
    python -m ingest.ingest --data-dir ../data --limit 100    # deneme

Yeniden çalıştırılabilir: (source, ref_id, chunk_index) benzersiz; ON CONFLICT ile güncellenir.
Embedding servisi (TEI) ve Postgres çalışıyor olmalı.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from app.embeddings import embed  # noqa: E402
from ingest.chunkers import Chunk, chunk_record, merged_title  # noqa: E402

SOURCES = ["meal", "tefsir", "hadis", "fetva", "dua", "ilmihal", "risale", "sorular", "dia"]
# TEI istek başına en fazla 32 metin kabul eder (max_client_batch_size); 32'yi aşınca 413.
# CPU'da büyük chunk'larda (tefsir/dia) yanıt yavaş, o yüzden varsayılan küçük tutulur.
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "16"))
# Bir embed isteği için timeout (sn) ve geçici hatada tekrar deneme sayısı.
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "300"))
EMBED_RETRIES = int(os.environ.get("EMBED_RETRIES", "5"))
UPSERT_BATCH = 500

UPSERT_SQL = """
INSERT INTO chunks (source, ref_id, chunk_index, title, url, content, meta, embedding)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8::vector)
ON CONFLICT (source, ref_id, chunk_index) DO UPDATE
SET title=EXCLUDED.title, url=EXCLUDED.url, content=EXCLUDED.content,
    meta=EXCLUDED.meta, embedding=EXCLUDED.embedding;
"""


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def iter_chunks(data_dir: Path, source: str, limit: int) -> list[tuple[str, Chunk]]:
    path = data_dir / f"{source}.jsonl"
    if not path.exists():
        print(f"  ! {path} yok, atlanıyor")
        return []
    out = []
    for n, line in enumerate(path.open(encoding="utf-8")):
        if limit and n >= limit:
            break
        rec = json.loads(line)
        for ch in chunk_record(source, rec):
            if ch.content.strip():
                out.append((source, ch))
    return _deduplicate(source, out)


def _deduplicate(source: str, chunks: list[tuple[str, Chunk]]) -> list[tuple[str, Chunk]]:
    """Collapse records carrying byte-identical text down to the first one.

    See chunkers.merged_title for the measurement. In short: a tefsir commentary is
    written for a RANGE of ayahs and published on each ayah's page, so the same text
    entered the corpus many times over; identical text has an identical embedding, so the
    twins filled top_k between them and made an answer look triply sourced.

    The survivor's title is rewritten to name every ayah covered, otherwise a citation
    reading "Yâsîn 34" would point at a commentary that also explains 35 and 36."""
    first: dict[str, tuple[str, Chunk]] = {}
    titles: dict[str, list[str]] = {}
    for item in chunks:
        key = hashlib.md5(item[1].content.encode("utf-8")).hexdigest()
        first.setdefault(key, item)
        if item[1].title:
            titles.setdefault(key, []).append(item[1].title)

    out = []
    for key, (src, chunk) in first.items():
        title = merged_title(source, titles.get(key, []))
        out.append((src, replace(chunk, title=title) if title else chunk))
    dropped = len(chunks) - len(out)
    if dropped:
        print(f"[{source}] {dropped} duplicate chunks collapsed")
    return out


async def _embed_with_retry(texts: list[str], client: httpx.AsyncClient) -> list[list[float]]:
    """Geçici TEI hatalarında (timeout, bağlantı, 5xx) tekrar dener; kalıcı hatada (4xx) hemen fırlatır."""
    for attempt in range(1, EMBED_RETRIES + 1):
        try:
            return await embed(texts, kind="passage", client=client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == EMBED_RETRIES:
                raise
            reason = f"HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            if attempt == EMBED_RETRIES:
                raise
            reason = type(exc).__name__
        wait = min(30, 3 * attempt)
        print(f"  embed hatası ({attempt}/{EMBED_RETRIES}): {reason}; {wait}s sonra tekrar",
              flush=True)
        await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


async def _embed_and_upsert(pool: asyncpg.Pool, client: httpx.AsyncClient,
                            batch: list[tuple[str, Chunk]]) -> None:
    vecs = await _embed_with_retry([c.content for _, c in batch], client)
    rows = [
        (src, c.ref_id, c.chunk_index, c.title, c.url, c.content,
         json.dumps(c.meta, ensure_ascii=False), _vec_literal(v))
        for (src, c), v in zip(batch, vecs)
    ]
    async with pool.acquire() as conn:
        await conn.executemany(UPSERT_SQL, rows)


async def run(data_dir: Path, sources: list[str], limit: int) -> None:
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=4)
    # Şemayı garantile (backend'den önce ingest çalıştırılırsa tablo yoksa oluşsun).
    schema_path = Path(__file__).resolve().parents[1] / "schema.sql"
    if schema_path.exists():
        async with pool.acquire() as conn:
            await conn.execute(schema_path.read_text(encoding="utf-8"))
    total = 0
    async with httpx.AsyncClient(timeout=EMBED_TIMEOUT, trust_env=False) as client:
        for source in sources:
            print(f"[{source}] bölümleniyor...")
            chunks = iter_chunks(data_dir, source, limit)
            # Devam edebilirlik: zaten yüklü chunk'ları atla → yeniden çalıştırma ucuz,
            # her deploy'da güvenle koşabilir (dolu DB'de saniyeler sürer).
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT ref_id, chunk_index FROM chunks WHERE source=$1", source)
            existing = {(r["ref_id"], r["chunk_index"]) for r in rows}
            if existing:
                before = len(chunks)
                chunks = [(s, c) for (s, c) in chunks
                          if (c.ref_id, c.chunk_index) not in existing]
                print(f"[{source}] {len(existing)} zaten yüklü, {before - len(chunks)} atlandı")
            print(f"[{source}] {len(chunks)} yeni chunk, embed + yükleme başlıyor")
            done = 0
            pending: list[tuple[str, Chunk]] = []
            for item in chunks:
                pending.append(item)
                if len(pending) >= EMBED_BATCH:
                    await _embed_and_upsert(pool, client, pending)
                    done += len(pending)
                    total += len(pending)
                    pending = []
                    if done % (EMBED_BATCH * 20) == 0:
                        print(f"[{source}]   {done}/{len(chunks)}")
            if pending:
                await _embed_and_upsert(pool, client, pending)
                done += len(pending)
                total += len(pending)
            print(f"[{source}] bitti: {done} chunk")
    await pool.close()
    print(f"TOPLAM {total} chunk yüklendi.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--source", choices=SOURCES, help="tek kaynak (varsayılan: hepsi)")
    ap.add_argument("--limit", type=int, default=0, help="kaynak başına kayıt sınırı (deneme)")
    args = ap.parse_args()
    sources = [args.source] if args.source else SOURCES
    asyncio.run(run(Path(args.data_dir), sources, args.limit))


if __name__ == "__main__":
    main()
