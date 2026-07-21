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
import json
import sys
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from app.embeddings import embed  # noqa: E402
from ingest.chunkers import Chunk, chunk_record  # noqa: E402

SOURCES = ["meal", "tefsir", "hadis", "fetva", "dia"]
EMBED_BATCH = 64
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
    return out


async def _embed_and_upsert(pool: asyncpg.Pool, client: httpx.AsyncClient,
                            batch: list[tuple[str, Chunk]]) -> None:
    vecs = await embed([c.content for _, c in batch], kind="passage", client=client)
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
    async with httpx.AsyncClient(timeout=180) as client:
        for source in sources:
            print(f"[{source}] bölümleniyor...")
            chunks = iter_chunks(data_dir, source, limit)
            print(f"[{source}] {len(chunks)} chunk, embed + yükleme başlıyor")
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
