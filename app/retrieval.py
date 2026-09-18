"""Hybrid retrieval: pgvector cosine + Turkish full-text, merged with RRF.

For religious texts, given Turkish morphology (suffixes) and terms of Arabic origin,
vector search alone is not enough; full-text search catches exact term matches. The two
rankings are blended with Reciprocal Rank Fusion.

Since the corpus is entirely Turkish, the search is done in Turkish too: non-Turkish
questions are first translated into Turkish (see _translate_to_turkish and
config.translate_queries).
"""
import logging
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass

from .config import settings
from .db import get_pool
from .embeddings import embed_one
from .intent import is_app_name_question
from .llm import complete

log = logging.getLogger("siraj")

RRF_K = 60  # RRF constant; a larger value softens rank differences


@dataclass
class Passage:
    source: str
    ref_id: str
    title: str | None
    url: str | None
    content: str
    meta: dict
    score: float


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# Turkish FTS query. websearch_to_tsquery ANDs the words (all must match).
# It used to be turned into an OR via '&' -> '|' (on the grounds that "AND is too strict
# for recall"); measured (2026-07-31), this poisoned the corpus: the 'bir' lexeme appears
# in 76% of chunks, so the OR query matched more than 200 thousand documents, and since
# ts_rank_cd rewards long documents, the same few huge fatwas made the top 3 on almost
# EVERY question. AND is precise; if nothing matches, the FTS leg stays empty and the
# vector alone decides the ranking - which beats noise.
_SQL = """
WITH q AS (
    SELECT $1::vector AS emb,
           websearch_to_tsquery('turkish', f_unaccent($2)) AS tsq
),
vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> (SELECT emb FROM q)) AS rnk
    FROM chunks
    WHERE embedding IS NOT NULL
      AND ($6::text[] IS NULL OR source = ANY($6))
    ORDER BY embedding <=> (SELECT emb FROM q)
    LIMIT $3
),
fts AS (
    SELECT id, row_number() OVER (
               ORDER BY ts_rank_cd(tsv, (SELECT tsq FROM q)) DESC) AS rnk
    FROM chunks
    WHERE (SELECT tsq FROM q) IS NOT NULL
      AND tsv @@ (SELECT tsq FROM q)
      AND ($6::text[] IS NULL OR source = ANY($6))
    LIMIT $3
),
fused AS (
    SELECT id, SUM(w) AS score FROM (
        SELECT id, 1.0 / ($4 + rnk) AS w FROM vec
        UNION ALL
        SELECT id, $7::float / ($4 + rnk) AS w FROM fts
    ) u
    GROUP BY id
)
SELECT c.source, c.ref_id, c.title, c.url, c.content, c.meta, f.score
FROM fused f
JOIN chunks c ON c.id = f.id
ORDER BY f.score DESC
LIMIT $5;
"""


_RELEVANCE_SQL = """
SELECT
    (SELECT MIN(embedding <=> $1::vector) FROM chunks) AS best_dist,
    EXISTS (
        SELECT 1 FROM chunks
        WHERE tsv @@ websearch_to_tsquery('turkish', f_unaccent($2))
    ) AS fts_hit;
"""


# When the user says what kind of source they want, we narrow the search to it.
# Reason (measured, 2026-07-31): for "Sabır hakkında bir ayet" all 6 nearest neighbours
# were DİA entries; the best verse, at 0.3055, never made top_k. This is not a ranking
# bug - an encyclopaedia entry explaining a concept really is closer in cosine than a
# SINGLE verse mentioning it (stripping the template prefix from the chunk makes it worse:
# 0.3743 -> 0.4407). If the user said "ayet" the intent is clear, so we use it.
_SOURCE_INTENT: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"\b(ayet|ayeti|ayette|ayetler|sure|suresi|verse|verses)\b"), ("meal", "tefsir")),
    (re.compile(r"\b(hadis|hadisi|hadiste|hadisler|hadith|hadiths|sunnet)\b"), ("hadis",)),
    (re.compile(r"\b(dua|duasi|duayi|dualar|supplication|supplications)\b"), ("dua",)),
    (re.compile(r"\b(fetva|fetvasi|fatwa)\b"), ("fetva",)),
]


# The app writes its own name in Latin letters everywhere as "Siraj"; the corpus writes
# the same word in classical Turkish spelling as "sirâc". f_unaccent folds 'â' to 'a' but
# cannot fold 'j' to 'c', so when a user typed the app's NAME verbatim nothing matched
# (measured 2026-08-07): "siraj ne demek" -> best_dist 0.8268, fts_hit False, relevance
# gate shut, answer "not in my sources". Spelled "sirac ne demek" the same question gives
# fts_hit True and puts the Ahzâb 46 passage in the top three - so the gap is in the
# spelling, not in the corpus.
#
# The table holds ONE entry on purpose. No measurement says the other romanizations that
# come to mind (hajj, jannah, dhikr...) fail, and English questions already pass through
# the translator; adding unmeasured entries would be adding untested surface. Whoever adds
# one owes the same before/after measurement as above.
#
# Suffixes are caught too ('siraj' + 'ın'/'ı'/'a'/'la'/'tan'...): Turkish is agglutinative
# and people write "Sirajın anlamı nedir?". A word boundary (\b...\b) saw none of those,
# only the apostrophe form "Siraj'ın". The suffix is preserved as typed; Postgres's Turkish
# stemmer reduces "sirâcın", "sirâcı" and "sirâca" to the 'siraç' stem (measured).
_ROMANIZATION = {"siraj": "sirâc"}

_ROMANIZATION_RE = re.compile(
    r"\b(" + "|".join(_ROMANIZATION) + r")'?(\w*)", re.IGNORECASE)

# The query used against the corpus when the app's name is asked about. We use this rather
# than the user's own phrasing because, measured on 2026-08-07, the full-text leg matches
# in this shape (`siraç & demek`, 7 chunks) and brings the Ahzâb 46 record into the top
# three, while the user's natural phrasings returned zero matches because of the AND.
_APP_NAME_SEARCH_QUERY = "sirâc ne demek"


def _fts_text(question: str) -> str:
    """The form of the query used by the full-text leg: romanization mapped to the corpus.

    Applied to the FULL-TEXT LEG ONLY; the vector leg and the text sent to the model keep
    the original. Reason (measured 2026-08-07): embedding the corrected text drops the
    nearest-neighbour distance for "Siraj ne anlama geliyor?" from 0.7538 to 0.3763, which
    opens the 0.42 relevance gate on its own - but the neighbours that arrive are Mİ‘RAC
    and SIRAT, entries near in spelling and unrelated in meaning. Applied to the vector
    leg, the correction shows the model a word it does not know as though it did, and the
    gate opens onto the wrong content; returning nothing is better than that. Romanization
    is a spelling correction, so it belongs to the leg that reads spelling.

    _detect_sources reads the ORIGINAL text as well: were an entry whose corrected form
    contains a word like "ayet"/"hadis" ever added to the table, the search would narrow to
    that source while the full-text leg searched for something else."""
    return _ROMANIZATION_RE.sub(
        lambda m: _ROMANIZATION[m.group(1).lower()] + m.group(2), question)


def _detect_sources(question: str) -> list[str] | None:
    """Returns the sources if the question explicitly asks for a source type, else None."""
    folded = question.lower().replace("ı", "i").replace("â", "a").replace("î", "i")
    folded = unicodedata.normalize("NFD", folded)
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    for pattern, sources in _SOURCE_INTENT:
        if pattern.search(folded):
            return list(sources)
    return None


# The corpus's fatwa/Q&A chunks are stored as "Soru: <title> Cevap: ...", so a question
# sentence dominates each chunk's vector; the search comes out markedly closer for
# questions phrased like the ones in the corpus. Measured (2026-08-03), "Kurban hakkında
# hüküm nedir?" was 0.7203 from its nearest neighbour and was turned away at the gate,
# while "Kurban kesmenin hükmü nedir?", asking the same thing, brought the right fatwas at
# 0.2935. So the translator is asked not for a literal translation but for a full question
# sentence, phrased as it would be asked on a Turkish Q&A site.
_TRANSLATE_SYSTEM = (
    "You translate a user's question into Turkish so it can be used as a search query "
    "over a Turkish corpus of Islamic sources. Output ONLY the Turkish translation, "
    "nothing else - no quotes, no explanation.\n"
    "Write it the way the question would be titled on a Turkish Islamic Q&A site: a "
    "complete, natural question ending in a question mark.\n"
    "Use the specific verb the act takes rather than a generic frame - 'kurban kesmenin "
    "hükmü nedir?' not 'kurban hakkında hüküm nedir?', 'evlenmenin şartları nelerdir?' "
    "not 'evlilik şartları nelerdir'.\n"
    "Keep religious terms in the classical Turkish form the sources use: ablution -> "
    "abdest, ritual bath -> gusül, fasting -> oruç, alms -> zekât, resurrection -> haşir, "
    "prayer -> namaz, pilgrimage -> hac, the hereafter -> ahiret."
)
# The app's built-in sample questions are the same on every launch; a small cache skips
# most translation calls entirely.
_TRANSLATION_CACHE: OrderedDict[str, str] = OrderedDict()
_CACHE_MAX = 256


async def _translate_to_turkish(question: str) -> str | None:
    """Translates the question into Turkish for search; returns None if it cannot."""
    key = question.strip()
    cached = _TRANSLATION_CACHE.get(key)
    if cached is not None:
        _TRANSLATION_CACHE.move_to_end(key)
        return cached
    try:
        turkish = await complete(
            [{"role": "system", "content": _TRANSLATE_SYSTEM},
             {"role": "user", "content": key}],
            max_tokens=settings.translate_max_tokens,
        )
    except Exception as exc:
        log.warning("query translation failed (%s), searching with the original question", exc)
        return None
    turkish = turkish.strip().strip('"').strip()
    if not turkish:
        return None
    _TRANSLATION_CACHE[key] = turkish
    if len(_TRANSLATION_CACHE) > _CACHE_MAX:
        _TRANSLATION_CACHE.popitem(last=False)
    return turkish


async def retrieve(question: str, lang: str = "tr") -> list[Passage]:
    import json

    # Search is always in Turkish (the corpus language); other languages are translated first.
    turkish_query = lang == "tr"

    # When the meaning of the app's name is asked, the search runs with the query that is
    # measured to work (see intent.is_app_name_question). The user's own sentence still
    # reaches the model separately; what changes here is only what we look for in the
    # corpus. Translation is skipped too: the query is already Turkish and already known.
    if is_app_name_question(question):
        question = _APP_NAME_SEARCH_QUERY
        turkish_query = True
    elif not turkish_query and settings.translate_queries:
        translated = await _translate_to_turkish(question)
        if translated:
            question = translated
            turkish_query = True

    fts_question = _fts_text(question)

    qvec = await embed_one(question, kind="query")
    pool = await get_pool()

    async with pool.acquire() as conn:
        relevance = await conn.fetchrow(_RELEVANCE_SQL, _vector_literal(qvec), fts_question)

    # Vector search works by nearest neighbour, so it returns something even for a
    # question that has nothing to do with the corpus. If there is neither semantic
    # closeness (distance under the threshold) nor a full-text match, the question is
    # unrelated to this corpus -> return nothing (so as not to show fake/unrelated sources;
    # see the empty-passages path in app/prompt.py).
    # The threshold and FTS were measured and tuned with Turkish queries. If the query
    # could not be translated into Turkish (translation off or failed) this gate does not
    # apply: measured (2026-07-31), relevant and unrelated did not separate for English
    # questions and FTS never matched. Rather than turn everyone away with a broken gate,
    # we skip it and pass the passages on; if they are unrelated, the model's "not in my
    # sources" rule takes over.
    best_dist = relevance["best_dist"]
    if turkish_query and (best_dist is None
                          or best_dist > settings.retrieval_max_distance) \
            and not relevance["fts_hit"]:
        return []

    # If the vector leg is of no use for this question (nearest neighbour beyond the
    # threshold), weight the full-text leg; otherwise stay at the measured 0.5.
    # See config.fts_weight_weak_vector.
    weak_vector = best_dist is None or best_dist > settings.retrieval_max_distance
    fts_weight = (settings.fts_weight_weak_vector if weak_vector
                  else settings.fts_weight)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SQL,
            _vector_literal(qvec),
            fts_question,
            settings.candidate_k,
            RRF_K,
            settings.top_k,
            _detect_sources(question),
            fts_weight,
        )

    out: list[Passage] = []
    for r in rows:
        meta = r["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        out.append(Passage(
            source=r["source"], ref_id=r["ref_id"], title=r["title"],
            url=r["url"], content=r["content"], meta=meta or {}, score=float(r["score"]),
        ))
    return out
