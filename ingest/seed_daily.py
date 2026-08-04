"""Günün kartlarının korpüsünü mobil paketten tohumlar (`daily_hadith`, `daily_dua`).

Kullanım:
    python -m ingest.seed_daily --mobile ../siraj-mobile
    python -m ingest.seed_daily --mobile ../siraj-mobile --dry-run

Neden buradan: kart korpüsü `chunks` üzerinden karşılanamaz — orası pencerelenmiş arama
metni; kartın istediği kısa söz + ravi + derece yalnızca uygulamanın paketinde var
(`src/data/hadiths.json`, `src/data/duas.json`). Ayrıntı: DAILY.md.

`ordinal` dosyadaki sıradır. Rotasyon (`gün % korpüs_boyu`) iki tarafta da bu sıraya
baktığı için uzak yol ile çevrimdışı yol aynı günde aynı kaydı seçer — tohumlamanın
doğruluk ölçütü budur, `--dry-run` bunu karşılaştırmadan önce gösterir.

Ingest gibi, YEREL makineden SSH tüneli üzerinden çalıştırılır (bkz. CLAUDE.md).
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
        raise SystemExit(f"Bulunamadı: {path}")
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise SystemExit(f"Boş ya da liste değil: {path}")
    missing = [i for i, r in enumerate(records) if not str(r.get("id", "")).strip()]
    if missing:
        raise SystemExit(f"{path}: id'siz kayıt(lar) var, sıra: {missing[:5]}")
    ids = [r["id"] for r in records]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{path}: id'ler benzersiz değil")
    return records


async def seed(mobile: Path, dry_run: bool) -> None:
    loaded = {table: load(mobile, rel) for table, rel in TABLES.items()}
    for table, records in loaded.items():
        print(f"{table}: {len(records)} kayıt  (ilk: {records[0]['id']}, "
              f"son: {records[-1]['id']})")
    if dry_run:
        print("\n--dry-run: veritabanına yazılmadı.")
        return

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        async with pool.acquire() as conn:
            await conn.execute(schema.read_text(encoding="utf-8"))

            for table, records in loaded.items():
                async with conn.transaction():
                    # Paketten düşen bir kayıt veritabanında kalırsa rotasyon iki tarafta
                    # ayrışır; bu yüzden tohumlama tam değişimdir, ekleme değil.
                    await conn.execute(f"DELETE FROM {table}")
                    await conn.executemany(
                        f"INSERT INTO {table} (id, ordinal, payload) VALUES ($1,$2,$3)",
                        [(r["id"], i, json.dumps(r, ensure_ascii=False))
                         for i, r in enumerate(records)],
                    )
                total = await conn.fetchval(f"SELECT count(*) FROM {table}")
                print(f"{table}: {total} kayıt yazıldı.")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mobile", default="../siraj-mobile",
                        help="siraj-mobile deposunun yolu")
    parser.add_argument("--dry-run", action="store_true",
                        help="yalnızca oku ve say, yazma")
    args = parser.parse_args()
    asyncio.run(seed(Path(args.mobile).resolve(), args.dry_run))


if __name__ == "__main__":
    main()
