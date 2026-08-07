#!/usr/bin/env python3
"""Korpüste birebir aynı içeriği taşıyan chunk'ları teke indirir.

    python -m ingest.dedupe_corpus                # kuru koşu (varsayılan), hiçbir şey silmez
    python -m ingest.dedupe_corpus --apply        # uygular
    python -m ingest.dedupe_corpus --source tefsir

Neden bir defalık betik: ingest artık aynı içeriği tekrar yüklemiyor (bkz.
ingest.ingest._tekille), ama önceden yüklenmiş satırlar kendiliğinden gitmiyor —
yeniden ingest, hayatta kalan kaydı zaten yüklü sayıp atlıyor ve ikizler yerinde kalıyor.

Silinen satırlar crawler'ın jsonl dosyalarından her zaman yeniden üretilebilir; bu işlem
veri kaybı değil, aynı metnin fazladan kopyalarının kaldırılmasıdır.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from ingest.chunkers import birlesik_baslik  # noqa: E402

# Hayatta kalan kayıt en küçük id'li olan: satırlar dosya sırasıyla yüklendiği için bu,
# ingest'in tekilleştirmede tuttuğu "ilk kayıt" ile aynı satırdır.
_GRUPLAR = """
SELECT md5(content) AS h,
       array_agg(id    ORDER BY id) AS idler,
       array_agg(title ORDER BY id) AS basliklar
FROM chunks
WHERE ($1::text IS NULL OR source = $1)
GROUP BY 1, source
HAVING count(*) > 1
"""


async def run(source: str | None, apply: bool) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        onceki = await conn.fetchval("SELECT count(*) FROM chunks")
        gruplar = await conn.fetch(_GRUPLAR, source)

        silinecek: list[int] = []
        yeni_baslik: list[tuple[str, int]] = []
        for g in gruplar:
            idler = list(g["idler"])
            silinecek.extend(idler[1:])
            kaynak = await conn.fetchval("SELECT source FROM chunks WHERE id=$1", idler[0])
            baslik = birlesik_baslik(kaynak, [b for b in g["basliklar"] if b])
            if baslik:
                yeni_baslik.append((baslik, idler[0]))

        print(f"korpüs            : {onceki} chunk")
        print(f"yinelenen grup    : {len(gruplar)}")
        print(f"silinecek satır   : {len(silinecek)}")
        print(f"künyesi düzelecek : {len(yeni_baslik)}")

        if not apply:
            print("\n(kuru koşu — hiçbir şey değiştirilmedi; uygulamak için --apply)")
            for baslik, kid in yeni_baslik[:5]:
                eski = await conn.fetchval("SELECT title FROM chunks WHERE id=$1", kid)
                print(f"  {eski}\n   -> {baslik}")
            return

        # Tek işlem: künyeler düzelmeden ikizler silinirse künye yanlış kalırdı.
        async with conn.transaction():
            await conn.executemany("UPDATE chunks SET title=$1 WHERE id=$2", yeni_baslik)
            await conn.execute("DELETE FROM chunks WHERE id = ANY($1::bigint[])", silinecek)

        sonraki = await conn.fetchval("SELECT count(*) FROM chunks")
        kalan = await conn.fetchval(
            "SELECT count(*) FROM (SELECT 1 FROM chunks GROUP BY md5(content), source"
            " HAVING count(*) > 1) t")
        print(f"\nbitti: {onceki} -> {sonraki} chunk ({onceki - sonraki} silindi)")
        print(f"kalan yinelenen grup: {kalan}")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="tek kaynak (varsayılan: hepsi)")
    ap.add_argument("--apply", action="store_true", help="değişiklikleri uygula")
    args = ap.parse_args()
    asyncio.run(run(args.source, args.apply))


if __name__ == "__main__":
    main()
