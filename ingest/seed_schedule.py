"""Write hand-picked days into `daily_schedule`.

Usage:
    python -m ingest.seed_schedule --file curation/schedule.json
    python -m ingest.seed_schedule --file curation/schedule.json --dry-run

The file lives under `curation/`, not `data/`: `data/` is the crawler's dump directory and
is gitignored, whereas curation must live in the repository.

Why curation lives in a file: `daily_schedule` rows are production data, invisible in the
code. Applying them from a committed file instead of writing INSERTs by hand keeps the
choices readable in a diff months later and brings them back with one command when the
database is rebuilt. A type left empty (hadis, dua) stays on rotation that day - this file
carries only the overrides, not the whole day.

Like ingest, this runs from the LOCAL machine over an SSH tunnel (see CLAUDE.md).
"""
import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402

FIELDS = ("ayah_global", "hadith_id", "dua_id")


def load(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not rows:
        raise SystemExit(f"Empty: {path}")

    seen: set[str] = set()
    for row in rows:
        try:
            dt.date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            raise SystemExit(f"Invalid date: {row!r}")
        if row["date"] in seen:
            raise SystemExit(f"Same day twice: {row['date']}")
        seen.add(row["date"])
        if not any(row.get(f) for f in FIELDS):
            raise SystemExit(f"{row['date']}: no field set, the row is meaningless")
    return rows


async def seed(path: Path, dry_run: bool) -> None:
    rows = load(path)
    print(f"{len(rows)} days, {rows[0]['date']} - {rows[-1]['date']}")
    for row in rows[:3]:
        print(f"  {row['date']}: {row.get('note', '')}")
    if len(rows) > 3:
        print(f"  … and {len(rows) - 3} more days")
    if dry_run:
        print("\n--dry-run: nothing written to the database.")
        return

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        async with pool.acquire() as conn:
            await conn.execute(schema.read_text(encoding="utf-8"))
            # Upsert: the file is the source, the database a copy. The file's date range
            # is cleared first so a day removed from the file does not linger here.
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM daily_schedule WHERE date BETWEEN $1 AND $2",
                    dt.date.fromisoformat(rows[0]["date"]),
                    dt.date.fromisoformat(rows[-1]["date"]),
                )
                await conn.executemany(
                    """INSERT INTO daily_schedule (date, ayah_global, hadith_id, dua_id, note)
                       VALUES ($1,$2,$3,$4,$5)""",
                    [(dt.date.fromisoformat(r["date"]), r.get("ayah_global"),
                      r.get("hadith_id"), r.get("dua_id"), r.get("note"))
                     for r in rows],
                )
            total = await conn.fetchval("SELECT count(*) FROM daily_schedule")
            print(f"daily_schedule: {total} rows.")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="curation/schedule.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(seed(Path(args.file).resolve(), args.dry_run))


if __name__ == "__main__":
    main()
