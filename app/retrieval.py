"""Hibrit getirim: pgvector kosinüs + Türkçe tam-metin, RRF ile birleştirme.

Dini metinlerde Türkçe morfoloji (ekler) ve Arapça kökenli terimler için tek başına
vektör araması yetmez; tam-metin araması tam terim eşleşmelerini yakalar. İkisinin
sırası Reciprocal Rank Fusion ile harmanlanır.
"""
from dataclasses import dataclass

from .config import settings
from .db import get_pool
from .embeddings import embed_one

RRF_K = 60  # RRF sabiti; büyük değer sıralama farklarını yumuşatır


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


# Türkçe FTS sorgusu: kullanıcı cümlesindeki kelimeleri OR'lu websearch sorgusuna çevirir.
_SQL = """
WITH -- websearch_to_tsquery kelimeleri AND'ler (hepsi eşleşmeli) — RAG recall'ı için çok katı.
-- '&' -> '|' ile OR'a çeviririz: herhangi bir terim eşleşsin, ts_rank_cd sıralasın.
q AS (
    SELECT $1::vector AS emb,
           NULLIF(replace(
               websearch_to_tsquery('turkish', f_unaccent($2))::text, '&', '|'
           ), '')::tsquery AS tsq
),
vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> (SELECT emb FROM q)) AS rnk
    FROM chunks
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> (SELECT emb FROM q)
    LIMIT $3
),
fts AS (
    SELECT id, row_number() OVER (
               ORDER BY ts_rank_cd(tsv, (SELECT tsq FROM q)) DESC) AS rnk
    FROM chunks
    WHERE (SELECT tsq FROM q) IS NOT NULL
      AND tsv @@ (SELECT tsq FROM q)
    LIMIT $3
),
fused AS (
    SELECT id, SUM(w) AS score FROM (
        SELECT id, 1.0 / ($4 + rnk) AS w FROM vec
        UNION ALL
        SELECT id, 1.0 / ($4 + rnk) AS w FROM fts
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


async def retrieve(question: str) -> list[Passage]:
    import json

    qvec = await embed_one(question, kind="query")
    pool = await get_pool()

    async with pool.acquire() as conn:
        relevance = await conn.fetchrow(_RELEVANCE_SQL, _vector_literal(qvec), question)

    # Vektör araması "en yakın komşu" mantığıyla çalıştığı için külliyatla hiç ilgisi
    # olmayan bir soruda bile bir şeyler döner. Ne semantik olarak yakın (mesafe eşiğin
    # altında) ne de tam-metin eşleşmesi varsa, bu soru bu külliyatla alakasızdır ->
    # boş dön (sahte/alakasız kaynak göstermemek için; bkz. app/prompt.py boş-passages yolu).
    best_dist = relevance["best_dist"]
    if (best_dist is None or best_dist > settings.retrieval_max_distance) and not relevance["fts_hit"]:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SQL,
            _vector_literal(qvec),
            question,
            settings.candidate_k,
            RRF_K,
            settings.top_k,
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
