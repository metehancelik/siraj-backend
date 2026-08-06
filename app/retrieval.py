"""Hibrit getirim: pgvector kosinüs + Türkçe tam-metin, RRF ile birleştirme.

Dini metinlerde Türkçe morfoloji (ekler) ve Arapça kökenli terimler için tek başına
vektör araması yetmez; tam-metin araması tam terim eşleşmelerini yakalar. İkisinin
sırası Reciprocal Rank Fusion ile harmanlanır.

Korpüs tamamen Türkçe olduğu için arama da Türkçe yapılır: Türkçe olmayan sorular
önce Türkçeye çevrilir (bkz. _translate_to_turkish ve config.translate_queries).
"""
import logging
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass

from .config import settings
from .db import get_pool
from .embeddings import embed_one
from .llm import complete

log = logging.getLogger("siraj")

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


# Türkçe FTS sorgusu. websearch_to_tsquery kelimeleri AND'ler (hepsi eşleşmeli).
# Eskiden '&' -> '|' ile OR'a çevriliyordu ("recall için AND çok katı" gerekçesiyle);
# ölçüldüğünde (2026-07-31) bunun korpüsü zehirlediği görüldü: 'bir' lexeme'i
# chunk'ların %76'sında geçtiği için OR sorgusu 200 binden fazla belge eşleştiriyor,
# ts_rank_cd de uzun belgeleri ödüllendirdiğinden aynı birkaç dev fetva neredeyse HER
# soruda ilk 3'e giriyordu. AND isabetli; hiç eşleşmezse FTS ayağı boş kalır ve
# sıralamayı yalnızca vektör belirler — bu, gürültüden iyidir.
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


# Kullanıcı ne tür bir kaynak istediğini söylediğinde aramayı oraya daraltırız.
# Gerekçe (ölçüm, 2026-07-31): "Sabır hakkında bir ayet" sorusunda en yakın 6 komşunun
# hepsi DİA maddesiydi; en iyi ayet 0.3055 ile top_k'ya hiç giremiyordu. Bu bir sıralama
# hatası değil — bir kavramı anlatan ansiklopedi maddesi, o kavramdan bahseden TEK bir
# ayetten kosinüs olarak gerçekten daha yakın (chunk'tan şablon öneki çıkarmak durumu
# kötüleştiriyor: 0.3743 -> 0.4407). Kullanıcı "ayet" dediyse niyeti açıktır, kullanırız.
_SOURCE_INTENT: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"\b(ayet|ayeti|ayette|ayetler|sure|suresi|verse|verses)\b"), ("meal", "tefsir")),
    (re.compile(r"\b(hadis|hadisi|hadiste|hadisler|hadith|hadiths|sunnet)\b"), ("hadis",)),
    (re.compile(r"\b(dua|duasi|duayi|dualar|supplication|supplications)\b"), ("dua",)),
    (re.compile(r"\b(fetva|fetvasi|fatwa)\b"), ("fetva",)),
]


# Uygulamanın adı her yerde Latin harfleriyle "Siraj" yazılı; korpüs aynı kelimeyi klasik
# Türkçe yazımıyla "sirâc" olarak yazıyor. f_unaccent 'â'yı 'a'ya katlar ama 'j'yi 'c'ye
# katlayamaz, dolayısıyla kullanıcı uygulamanın ADINI aynen yazdığında hiçbir şey
# eşleşmiyordu (ölçüm 2026-08-07): "siraj ne demek" -> best_dist 0.8268, fts_hit False,
# alaka kapısı kapalı, cevap "kaynaklarda bulamadım". Aynı soru "sirac ne demek" yazıldığında
# fts_hit True ve Ahzab 46 pasajı ilk üçte geliyor — yani boşluk korpüste değil, yazımda.
#
# Tablo bilerek TEK kayıtlık. Akla gelen diğer romanizasyonların (hajj, jannah, dhikr...)
# başarısız olduğuna dair bir ölçüm yok ve İngilizce sorular zaten çeviriden geçiyor;
# ölçülmemiş kayıt eklemek sınanmamış yüzey demek. Yeni kayıt ekleyen, yukarıdakinin aynısı
# bir önce/sonra ölçümünü yapmalı.
_ROMANIZASYON = {"siraj": "sirâc"}
_ROMANIZASYON_RE = re.compile(
    r"\b(" + "|".join(_ROMANIZASYON) + r")\b", re.IGNORECASE)


def _fts_metni(question: str) -> str:
    """Sorgunun tam-metin ayağında kullanılacak hâli: romanizasyon korpüsün yazımına çevrilir.

    YALNIZCA FTS ayağına uygulanır; vektör ayağı ve modele giden metin özgün hâlinde kalır.
    Gerekçe (ölçüm 2026-08-07): düzeltilmiş metin embed edildiğinde "Siraj ne anlama
    geliyor?" sorusunun en yakın komşu mesafesi 0.7538'den 0.3763'e düşüyor ve 0.42'lik
    alaka kapısını TEK BAŞINA açıyor — ama gelen komşular Mİ‘RAC ve SIRAT, yani yazımca
    benzer, anlamca alakasız maddeler. Yani düzeltme vektör ayağına uygulandığında modele
    bilmediği bir kelimeyi biliyormuş gibi gösteriyor ve kapı yanlış içeriğe açılıyor;
    boş dönmek bundan iyidir. Romanizasyon yazımla ilgili bir düzeltmedir, dolayısıyla
    yazıma bakan ayağa aittir."""
    return _ROMANIZASYON_RE.sub(lambda m: _ROMANIZASYON[m.group(0).lower()], question)


def _detect_sources(question: str) -> list[str] | None:
    """Soru açıkça bir kaynak türü istiyorsa o kaynakları döner, aksi halde None."""
    folded = question.lower().replace("ı", "i").replace("â", "a").replace("î", "i")
    folded = unicodedata.normalize("NFD", folded)
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    for pattern, sources in _SOURCE_INTENT:
        if pattern.search(folded):
            return list(sources)
    return None


# Korpüsün fetva/soru-cevap parçaları "Soru: <başlık> Cevap: ..." biçiminde saklandığı
# için her parçanın vektörüne bir soru cümlesi hâkim; arama, korpüstekine benzer kurulmuş
# sorulara belirgin biçimde yakın çıkıyor. Ölçüldüğünde (2026-08-03) "Kurban hakkında
# hüküm nedir?" en yakın komşuya 0.7203 uzaklıktaydı ve kapıdan dönüyordu; aynı şeyi
# soran "Kurban kesmenin hükmü nedir?" ise 0.2935 ile doğru fetvaları getiriyordu. Bu
# yüzden çevirmenden düz bir çeviri değil, Türkçe bir soru-cevap sitesinde sorulacak
# biçimde tam bir soru cümlesi isteniyor.
_TRANSLATE_SYSTEM = (
    "You translate a user's question into Turkish so it can be used as a search query "
    "over a Turkish corpus of Islamic sources. Output ONLY the Turkish translation, "
    "nothing else — no quotes, no explanation.\n"
    "Write it the way the question would be titled on a Turkish Islamic Q&A site: a "
    "complete, natural question ending in a question mark.\n"
    "Use the specific verb the act takes rather than a generic frame — 'kurban kesmenin "
    "hükmü nedir?' not 'kurban hakkında hüküm nedir?', 'evlenmenin şartları nelerdir?' "
    "not 'evlilik şartları nelerdir'.\n"
    "Keep religious terms in the classical Turkish form the sources use: ablution -> "
    "abdest, ritual bath -> gusül, fasting -> oruç, alms -> zekât, resurrection -> haşir, "
    "prayer -> namaz, pilgrimage -> hac, the hereafter -> ahiret."
)
# Uygulamanın hazır örnek soruları her açılışta aynı; küçük bir önbellek çeviri
# çağrısının çoğunu tamamen atlatır.
_TRANSLATION_CACHE: OrderedDict[str, str] = OrderedDict()
_CACHE_MAX = 256


async def _translate_to_turkish(question: str) -> str | None:
    """Soruyu arama için Türkçeye çevirir; çeviri yapılamazsa None döner."""
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
        log.warning("sorgu çevirisi başarısız (%s), özgün soruyla aranıyor", exc)
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

    # Arama daima Türkçe yapılır (korpüsün dili); soru başka dildeyse önce çevrilir.
    turkish_query = lang == "tr"
    if not turkish_query and settings.translate_queries:
        translated = await _translate_to_turkish(question)
        if translated:
            question = translated
            turkish_query = True

    fts_question = _fts_metni(question)

    qvec = await embed_one(question, kind="query")
    pool = await get_pool()

    async with pool.acquire() as conn:
        relevance = await conn.fetchrow(_RELEVANCE_SQL, _vector_literal(qvec), fts_question)

    # Vektör araması "en yakın komşu" mantığıyla çalıştığı için külliyatla hiç ilgisi
    # olmayan bir soruda bile bir şeyler döner. Ne semantik olarak yakın (mesafe eşiğin
    # altında) ne de tam-metin eşleşmesi varsa, bu soru bu külliyatla alakasızdır ->
    # boş dön (sahte/alakasız kaynak göstermemek için; bkz. app/prompt.py boş-passages yolu).
    # Eşik ve FTS Türkçe sorgularla ölçülüp ayarlandı. Sorgu Türkçeye çevrilemediyse
    # (çeviri kapalı veya hata verdi) bu kapı geçerli değil: ölçüldüğünde (2026-07-31)
    # İngilizce sorularda alakalı ile alakasız ayrışmıyordu ve FTS hiç eşleşmiyordu.
    # Bozuk bir kapıyla herkesi geri çevirmektense kapıyı atlayıp pasajları veriyoruz;
    # alakasızsa modelin "kaynaklarda bulamadım" kuralı devreye girer.
    best_dist = relevance["best_dist"]
    if turkish_query and (best_dist is None
                          or best_dist > settings.retrieval_max_distance) \
            and not relevance["fts_hit"]:
        return []

    # Vektör ayağı bu soruda işe yaramıyorsa (en yakın komşu eşiğin ötesinde) tam-metin
    # ayağına ağırlık ver; aksi halde ölçülmüş 0.5'te kal. Bkz. config.fts_weight_weak_vector.
    vektor_zayif = best_dist is None or best_dist > settings.retrieval_max_distance
    fts_agirlik = (settings.fts_weight_weak_vector if vektor_zayif
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
            fts_agirlik,
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
