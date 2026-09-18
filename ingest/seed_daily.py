"""Seed the daily card corpus (`daily_hadith`, `daily_dua`) from the mobile bundle.

Usage:
    python -m ingest.seed_daily --mobile ../siraj-mobile
    python -m ingest.seed_daily --mobile ../siraj-mobile --dry-run

Why from here: the card corpus cannot be served from `chunks` - that is windowed search
text; the short saying + narrator + grade a card needs exists only in the app bundle
(`src/data/hadiths.json`, `src/data/duas.json`). Details: DAILY.md.

`ordinal` is the position in the file. Rotation (`day % corpus_size`) reads this order on
both sides, so the remote path and the offline path pick the same record on the same
day - that is the correctness criterion for seeding, and `--dry-run` shows it before you
compare.

Like ingest, this runs from the LOCAL machine over an SSH tunnel (see CLAUDE.md).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402

TABLES = {
    "daily_hadith": "src/data/hadiths.json",
    "daily_dua": "src/data/duas.json",
}


def load(mobile: Path, relative: str) -> list[dict]:
    path = mobile / relative
    if not path.exists():
        raise SystemExit(f"Not found: {path}")
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise SystemExit(f"Empty or not a list: {path}")
    missing = [i for i, r in enumerate(records) if not str(r.get("id", "")).strip()]
    if missing:
        raise SystemExit(f"{path}: record(s) without an id, at positions: {missing[:5]}")
    ids = [r["id"] for r in records]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{path}: ids are not unique")
    return records


async def seed(mobile: Path, dry_run: bool) -> None:
    loaded = {table: load(mobile, rel) for table, rel in TABLES.items()}
    for table, records in loaded.items():
        print(f"{table}: {len(records)} records  (first: {records[0]['id']}, "
              f"last: {records[-1]['id']})")
    if dry_run:
        print("\n--dry-run: nothing written to the database.")
        return

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        async with pool.acquire() as conn:
            await conn.execute(schema.read_text(encoding="utf-8"))

            for table, records in loaded.items():
                async with conn.transaction():
                    # If a record dropped from the bundle stayed in the database, the
                    # rotation would diverge between the two sides; so seeding is a full
                    # replace, not an append.
                    #
                    # The cost: the `daily_schedule` FKs are ON DELETE SET NULL, so this
                    # DELETE silently nulls the pinned hadith_id/dua_id fields - no error,
                    # no change in output. `seed_schedule` must therefore be run AFTERWARDS;
                    # otherwise the curated days fall back to rotation.
                    await conn.execute(f"DELETE FROM {table}")
                    await conn.executemany(
                        f"INSERT INTO {table} (id, ordinal, payload) VALUES ($1,$2,$3)",
                        [(r["id"], i, json.dumps(r, ensure_ascii=False))
                         for i, r in enumerate(records)],
                    )
                total = await conn.fetchval(f"SELECT count(*) FROM {table}")
                print(f"{table}: {total} records written.")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mobile", default="../siraj-mobile",
                        help="path to the siraj-mobile repository")
    parser.add_argument("--dry-run", action="store_true",
                        help="only read and count, do not write")
    args = parser.parse_args()
    try:
        asyncio.run(seed(Path(args.mobile).resolve(), args.dry_run))
    except Exception as exc:  # noqa: BLE001 - the exit code matters
        # A silent failure is the worst case: it happened once, the seeding was rolled
        # back but the summary lines had already been printed, and the database was left
        # with the old corpus.
        raise SystemExit(f"SEEDING FAILED: {exc}")


if __name__ == "__main__":
    main()
