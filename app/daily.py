"""Günün ayeti, hadisi ve duası — hangisi olduğunu seçer, içeriği derler.

Sözleşme ve gerekçeler: DAILY.md. Özet: seçim burada merkezîleşir, uygulama yine de
paketindeki korpüsle çevrimdışı çalışabilir; iki taraf aynı gün aynı kaydı seçsin diye
rotasyon birebir aynı aritmetiktir.
"""
import datetime as dt
import json
import logging

import asyncpg
import httpx

from .db import get_pool

log = logging.getLogger("siraj")

TOTAL_AYAHS = 6236

# Uygulamanın `quranService.ts`'te kullandığı sürümlerin aynısı. tr.vakfi (Diyanet Vakfı
# meali) bilerek korunuyor: uç noktanın döndürdüğü ayet, cihazın bugün doğrudan aldığının
# birebir aynısı olmalı — değişen tek şey isteği kimin yaptığı.
_EDITIONS = "quran-uthmani,en.asad,tr.vakfi"
_ARABIC_EDITION = "quran-uthmani"
_ENGLISH_EDITION = "en.asad"
_TURKISH_EDITION = "tr.vakfi"

_EPOCH = dt.date(1970, 1, 1)


class DailyUnavailable(RuntimeError):
    """Kart derlenemedi. Uygulama sessizce kendi yerel yoluna düşer, kullanıcıya hata
    gösterilmez — gösterilecek içerik zaten vardır."""


def days_since_epoch(day: dt.date) -> int:
    """Mobildeki `daysSinceEpoch` ile aynı sayı.

    Orada yerel gece yarısına göre hesaplanır ve tarih bize zaten istemcinin YEREL günü
    olarak gelir (yolun içinde), dolayısıyla takvim günü farkını almak yeter. Sunucunun
    kendi `now()`'ı hiçbir yerde kullanılmaz: okuyucunun saat dilimini bilemez.
    """
    return day.toordinal() - _EPOCH.toordinal()


async def _fetch_ayah_from_source(global_number: int) -> dict:
    url = f"https://api.alquran.cloud/v1/ayah/{global_number}/editions/{_EDITIONS}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    items = {item["edition"]["identifier"]: item for item in payload.get("data", [])}
    arabic = items.get(_ARABIC_EDITION)
    turkish = items.get(_TURKISH_EDITION)
    if not arabic or not turkish:
        raise DailyUnavailable(f"Ayet {global_number}: Arapça veya Türkçe sürüm gelmedi")

    return {
        "global_number": global_number,
        "surah_number": arabic["surah"]["number"],
        "number_in_surah": arabic["numberInSurah"],
        "surah_name_ar": arabic["surah"]["name"],
        "surah_name_en": arabic["surah"]["englishName"],
        "arabic": arabic["text"],
        "translation_tr": turkish["text"],
        "translation_en": (items.get(_ENGLISH_EDITION) or {}).get("text"),
    }


async def _ayah(conn: asyncpg.Connection, global_number: int) -> dict:
    """Ayeti tablodan verir; yoksa kaynağından bir kez alıp yazar.

    Yazma çakışması sorun değil: aynı ayeti iki istek birden çekerse ikisi de aynı metni
    yazar (ON CONFLICT DO NOTHING).
    """
    row = await conn.fetchrow("SELECT * FROM ayah_text WHERE global_number = $1", global_number)
    if row is None:
        fetched = await _fetch_ayah_from_source(global_number)
        await conn.execute(
            """INSERT INTO ayah_text (global_number, surah_number, number_in_surah,
                                      surah_name_ar, surah_name_en, arabic,
                                      translation_tr, translation_en)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (global_number) DO NOTHING""",
            fetched["global_number"], fetched["surah_number"], fetched["number_in_surah"],
            fetched["surah_name_ar"], fetched["surah_name_en"], fetched["arabic"],
            fetched["translation_tr"], fetched["translation_en"],
        )
        row = fetched

    # Alanlar mobildeki AyahContent ile birebir; uygulama ek dönüşüm yapmaz.
    return {
        "globalNumber": row["global_number"],
        "surahNumber": row["surah_number"],
        "numberInSurah": row["number_in_surah"],
        "surahNameArabic": row["surah_name_ar"],
        "surahNameEnglish": row["surah_name_en"],
        "arabic": row["arabic"],
        "translationTr": row["translation_tr"],
        "translationEn": row["translation_en"] or "",
    }


def _payload(value) -> dict | None:
    """asyncpg jsonb'yi metin olarak döndürür (retrieval.py'de de böyle çözülüyor)."""
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


async def _rotating(conn: asyncpg.Connection, table: str, day: int) -> dict:
    """`table`'dan günün kaydı: paketteki sırayla aynı rotasyon."""
    total = await conn.fetchval(f"SELECT count(*) FROM {table}")
    if not total:
        raise DailyUnavailable(f"{table} boş — tohumlama yapılmamış (bkz. DAILY.md)")
    payload = await conn.fetchval(
        f"SELECT payload FROM {table} WHERE ordinal = $1", day % total
    )
    if payload is None:
        raise DailyUnavailable(f"{table}: {day % total}. sıra yok, tohumlama eksik")
    return _payload(payload)


async def _pinned(conn: asyncpg.Connection, table: str, record_id: str) -> dict | None:
    return _payload(await conn.fetchval(f"SELECT payload FROM {table} WHERE id = $1", record_id))


async def build_daily(day: dt.date) -> dict:
    """Verilen YEREL takvim günü için üç kartı derler.

    Önce `daily_schedule`'a bakılır; satır yoksa (ya da bir alanı boşsa) deterministik
    rotasyona düşülür — uygulamanın çevrimdışıyken yaptığının aynısı. Bu yüzden her günü
    elle doldurmak gerekmez.
    """
    index = days_since_epoch(day)
    pool = await get_pool()
    async with pool.acquire() as conn:
        pinned = await conn.fetchrow(
            "SELECT ayah_global, hadith_id, dua_id FROM daily_schedule WHERE date = $1", day
        )

        ayah_global = (pinned and pinned["ayah_global"]) or (index % TOTAL_AYAHS) + 1

        hadith = None
        dua = None
        if pinned and pinned["hadith_id"]:
            hadith = await _pinned(conn, "daily_hadith", pinned["hadith_id"])
        if pinned and pinned["dua_id"]:
            dua = await _pinned(conn, "daily_dua", pinned["dua_id"])

        hadith = hadith or await _rotating(conn, "daily_hadith", index)
        dua = dua or await _rotating(conn, "daily_dua", index)
        ayah = await _ayah(conn, ayah_global)

    return {"date": day.isoformat(), "ayah": ayah, "hadith": hadith, "dua": dua}
