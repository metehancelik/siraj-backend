"""Verse, hadith and supplication of the day - picks which one it is and assembles it.

Contract and rationale: DAILY.md. In short: the choice is centralised here, yet the app can
still work offline with the corpus in its bundle; the rotation is the exact same
arithmetic on both sides so that they pick the same record on the same day.
"""
import datetime as dt
import json
import logging

import asyncpg
import httpx

from .db import get_pool

log = logging.getLogger("siraj")

TOTAL_AYAHS = 6236

# The same editions the app uses in `quranService.ts`. The English translation is Saheeh
# International: Asad treats verses as continuations of the previous one, starting them in
# lowercase, and reads archaic, which is wrong for a card that stands alone. tr.vakfi
# (the Diyanet Vakfı meal) is kept on purpose: the verse the endpoint returns must be
# identical to what the device fetches directly today - the only change is who makes the
# request.
_EDITIONS = "quran-uthmani,en.sahih,tr.vakfi"
_ARABIC_EDITION = "quran-uthmani"
_ENGLISH_EDITION = "en.sahih"
_TURKISH_EDITION = "tr.vakfi"

_EPOCH = dt.date(1970, 1, 1)


class DailyUnavailable(RuntimeError):
    """The card could not be assembled. The app silently falls back to its local path and
    no error is shown to the user - there is content to show anyway."""


def days_since_epoch(day: dt.date) -> int:
    """The same number as `daysSinceEpoch` on mobile.

    There it is computed against local midnight, and the date reaches us already as the
    client's LOCAL day (in the path), so the calendar-day difference is enough. The
    server's own `now()` is never used: it cannot know the reader's time zone.
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
        raise DailyUnavailable(f"Ayah {global_number}: Arabic or Turkish edition missing")

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
    """Returns the verse from the table; if missing, fetches it once from the source and
    stores it.

    A write race is harmless: if two requests fetch the same verse at once, both write the
    same text (ON CONFLICT DO NOTHING).
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

    # Fields match AyahContent on mobile exactly; the app does no extra mapping.
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
    """asyncpg returns jsonb as text (retrieval.py decodes it the same way)."""
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


# If the English pool is smaller than this, the card is not shown in English at all: a
# card that repeats within two weeks stops being the card "of the day". Showing Turkish
# text in the English interface is not an option either. The number was chosen from this
# principle, not from the data, and must match MIN_ENGLISH_POOL on mobile.
MIN_POOL = 14


async def _rotating(
    conn: asyncpg.Connection, table: str, day: int, english_field: str | None
) -> dict | None:
    """The day's record from `table`: the same rotation as the order in the bundle.

    When `english_field` is given, the pool narrows to records with that field filled and
    the order walks over that narrowed set. If the pool is below `MIN_POOL` the card is
    treated as absent (None) - the caller drops it from the response.
    """
    if english_field:
        rows = await conn.fetch(
            f"""SELECT payload FROM {table}
                 WHERE coalesce(payload ->> $1, '') <> ''
                 ORDER BY ordinal""",
            english_field,
        )
        if len(rows) < MIN_POOL:
            return None
        return _payload(rows[day % len(rows)]["payload"])

    total = await conn.fetchval(f"SELECT count(*) FROM {table}")
    if not total:
        raise DailyUnavailable(f"{table} is empty - not seeded (see DAILY.md)")
    payload = await conn.fetchval(
        f"SELECT payload FROM {table} WHERE ordinal = $1", day % total
    )
    if payload is None:
        raise DailyUnavailable(f"{table}: no ordinal {day % total}, seeding incomplete")
    return _payload(payload)


async def _pinned(conn: asyncpg.Connection, table: str, record_id: str) -> dict | None:
    return _payload(await conn.fetchval(f"SELECT payload FROM {table} WHERE id = $1", record_id))


async def build_daily(day: dt.date, lang: str = "tr") -> dict:
    """Assembles the three cards for the given LOCAL calendar day.

    `daily_schedule` is checked first; if there is no row (or a field is empty) it falls
    back to the deterministic rotation - the same thing the app does offline. So not every
    day has to be filled in by hand.
    """
    index = days_since_epoch(day)
    english = lang.lower().startswith("en")
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

        hadith = hadith or await _rotating(
            conn, "daily_hadith", index, "textEn" if english else None
        )
        dua = dua or await _rotating(conn, "daily_dua", index, "english" if english else None)
        ayah = await _ayah(conn, ayah_global)

    # The verse exists in every language (all three editions are stored together); hadith
    # and supplication return null when the English pool is too small, and the app does
    # not draw that card at all.
    return {"date": day.isoformat(), "lang": "en" if english else "tr",
            "ayah": ayah, "hadith": hadith, "dua": dua}
