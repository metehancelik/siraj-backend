"""Elle seçilmiş günleri `daily_schedule`'a yazar.

Kullanım:
    python -m ingest.seed_schedule --file curation/schedule.json
    python -m ingest.seed_schedule --file curation/schedule.json --dry-run

Dosya `curation/` altında, `data/` altında değil: `data/` crawler'ın döküm dizini ve
gitignore'da: küratörlük tam tersine depoda durmalı.

Küratörlük neden bir dosyada: `daily_schedule` satırları üretim verisidir, kodda görünmez.
Doğrudan INSERT yazmak yerine depoya işlenmiş bir dosyadan uygulamak, seçimin aylar sonra
diff'te okunabilmesini ve veritabanı yeniden kurulduğunda tek komutla geri gelmesini
sağlıyor. Alanı boş bırakılan tür (hadis, dua) o gün rotasyonda kalır — bu dosya yalnızca
müdahaleyi taşır, günün tamamını değil.

Ingest gibi, YEREL makineden SSH tüneli üzerinden çalıştırılır (bkz. CLAUDE.md).
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
        raise SystemExit(f"Bulunamadı: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not rows:
        raise SystemExit(f"Boş: {path}")

    seen: set[str] = set()
    for row in rows:
        try:
            dt.date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            raise SystemExit(f"Geçersiz tarih: {row!r}")
        if row["date"] in seen:
            raise SystemExit(f"Aynı gün iki kez: {row['date']}")
        seen.add(row["date"])
        if not any(row.get(f) for f in FIELDS):
            raise SystemExit(f"{row['date']}: hiçbir alan seçilmemiş, satırın anlamı yok")
    return rows


async def seed(path: Path, dry_run: bool) -> None:
    rows = load(path)
    print(f"{len(rows)} gün, {rows[0]['date']} — {rows[-1]['date']}")
    for row in rows[:3]:
        print(f"  {row['date']}: {row.get('note', '')}")
    if len(rows) > 3:
        print(f"  … ve {len(rows) - 3} gün daha")
    if dry_run:
        print("\n--dry-run: veritabanına yazılmadı.")
        return

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        async with pool.acquire() as conn:
            await conn.execute(schema.read_text(encoding="utf-8"))
            # Upsert: dosya kaynak, veritabanı kopya. Dosyadan çıkarılan bir gün burada
            # kalmasın diye dosyadaki aralık önce temizlenir.
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
            print(f"daily_schedule: {total} satır.")
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
