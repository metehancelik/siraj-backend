#!/usr/bin/env python3
"""Collapse byte-identical chunks in the corpus down to one row each.

    python -m ingest.dedupe_corpus                # dry run (the default), changes nothing
    python -m ingest.dedupe_corpus --apply
    python -m ingest.dedupe_corpus --source tefsir

Why a one-off script: ingest no longer loads the same text twice (see
ingest.ingest._deduplicate), but rows loaded before that fix do not disappear on their
own - a re-run sees the surviving row as already present, skips it, and leaves the twins
in place.

Deleted rows can always be rebuilt from the crawler's jsonl files; this removes extra
copies of the same text, not data.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from ingest.chunkers import merged_title  # noqa: E402

# The survivor is the row with the smallest id. Rows are loaded in file order, so that is
# the same row ingest keeps when it deduplicates.
_DUPLICATE_GROUPS = """
SELECT md5(content) AS hash,
       array_agg(id    ORDER BY id) AS ids,
       array_agg(title ORDER BY id) AS titles
FROM chunks
WHERE ($1::text IS NULL OR source = $1)
GROUP BY 1, source
HAVING count(*) > 1
"""


async def run(source: str | None, apply: bool) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        before = await conn.fetchval("SELECT count(*) FROM chunks")
        groups = await conn.fetch(_DUPLICATE_GROUPS, source)

        doomed: list[int] = []
        retitled: list[tuple[str, int]] = []
        for group in groups:
            ids = list(group["ids"])
            doomed.extend(ids[1:])
            group_source = await conn.fetchval(
                "SELECT source FROM chunks WHERE id=$1", ids[0])
            title = merged_title(group_source, [t for t in group["titles"] if t])
            if title:
                retitled.append((title, ids[0]))

        print(f"corpus            : {before} chunks")
        print(f"duplicate groups  : {len(groups)}")
        print(f"rows to delete    : {len(doomed)}")
        print(f"titles to rewrite : {len(retitled)}")

        if not apply:
            print("\n(dry run - nothing changed; pass --apply to carry it out)")
            for title, kept_id in retitled[:5]:
                old = await conn.fetchval("SELECT title FROM chunks WHERE id=$1", kept_id)
                print(f"  {old}\n   -> {title}")
            return

        # One transaction: deleting the twins before the titles are widened would leave
        # the survivor labelled with a single ayet it no longer stands for.
        async with conn.transaction():
            await conn.executemany("UPDATE chunks SET title=$1 WHERE id=$2", retitled)
            await conn.execute("DELETE FROM chunks WHERE id = ANY($1::bigint[])", doomed)

        after = await conn.fetchval("SELECT count(*) FROM chunks")
        remaining = await conn.fetchval(
            "SELECT count(*) FROM (SELECT 1 FROM chunks GROUP BY md5(content), source"
            " HAVING count(*) > 1) t")
        print(f"\ndone: {before} -> {after} chunks ({before - after} deleted)")
        print(f"duplicate groups left: {remaining}")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="a single source (default: all)")
    ap.add_argument("--apply", action="store_true", help="carry out the changes")
    args = ap.parse_args()
    asyncio.run(run(args.source, args.apply))


if __name__ == "__main__":
    main()
