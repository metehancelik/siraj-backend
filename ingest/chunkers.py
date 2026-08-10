"""Kaynak-farkında bölümleme (chunking).

Her kaynağın doğal yapısı farklı, tek bir stratejiyle bölmek kaliteyi düşürür:
- fetva: zaten kısa Soru/Cevap birimi  -> bütün bırak
- sorular: Soru/Cevap, ama cevaplar uzun -> pencerele, soruyu her pencereye yaz
- meal : ayet/ayet-grubu birimleri      -> ayet başına chunk
- tefsir: ayet tefsiri                   -> kısa ise bütün, uzunsa pencerele
- hadis: dev cilt metinleri (PDF)        -> pencerele
- dia  : ansiklopedi maddeleri (dev)     -> pencerele
- risale: kitap bölümleri (1-148 sayfa)  -> pencerele, bölüm yolunu metne yaz
- ilmihal: kitap bölümleri (PDF)         -> pencerele, bölüm yolunu metne yaz
"""
import re
import unicodedata
from dataclasses import dataclass

TARGET_CHARS = 1100   # ~300 token hedef
OVERLAP_CHARS = 180
MAX_WHOLE = 1600      # bu sınırın altındaki kayıtlar bölünmeden bırakılır


@dataclass
class Chunk:
    ref_id: str
    chunk_index: int
    title: str | None
    url: str | None
    content: str
    meta: dict


def _window(text: str) -> list[str]:
    """Metni paragraf/cümle sınırlarına saygı göstererek pencerelere böler."""
    text = text.strip()
    if len(text) <= MAX_WHOLE:
        return [text]
    # Önce paragraflara ayır, sonra hedef boyuta kadar birleştir.
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(para) > TARGET_CHARS:
            # Çok uzun paragrafı cümlelere kır
            for piece in _split_long(para):
                buf = _append(chunks, buf, piece)
        else:
            buf = _append(chunks, buf, para)
    if buf.strip():
        chunks.append(buf.strip())
    return _add_overlap(chunks)


def _append(chunks: list[str], buf: str, piece: str) -> str:
    if len(buf) + len(piece) + 1 <= TARGET_CHARS:
        return f"{buf} {piece}".strip()
    if buf.strip():
        chunks.append(buf.strip())
    return piece


def _split_long(para: str) -> list[str]:
    import re
    sentences = re.split(r"(?<=[.!?…])\s+", para)
    out, buf = [], ""
    for s in sentences:
        if len(buf) + len(s) + 1 <= TARGET_CHARS:
            buf = f"{buf} {s}".strip()
        else:
            if buf:
                out.append(buf)
            buf = s
    if buf:
        out.append(buf)
    return out


def _add_overlap(chunks: list[str]) -> list[str]:
    if OVERLAP_CHARS <= 0 or len(chunks) < 2:
        return chunks
    out = [chunks[0]]
    for prev, cur in zip(chunks, chunks[1:]):
        tail = prev[-OVERLAP_CHARS:]
        out.append((tail + " " + cur).strip())
    return out


_DIA_FOOTER = re.compile(r"\nBu madde TDV İslâm Ansiklopedisi'?nin.*$", re.S)
_DIA_AUTHOR = re.compile(r"Müellif:\s*\n\s*([^\n]+)")


def clean_dia(text: str) -> tuple[str, str | None]:
    """DIA maddesinden şablon başlık/dipnotu ayıklar; (temiz_metin, müellif) döner."""
    author = None
    m = _DIA_AUTHOR.search(text)
    if m:
        author = m.group(1).strip()
    # Gerçek gövde "Web Adresi:\n<url>\n" bloğundan sonra başlar.
    parts = text.split("Web Adresi:\n", 1)
    body = parts[1] if len(parts) == 2 else text
    body = body.split("\n", 1)[1] if "\n" in body else body  # URL satırını at
    body = _DIA_FOOTER.sub("", body)                          # matbu dipnotunu at
    # Atıf satırı ("...(tarih). Kopyalama metni") gerçek gövdeden önce gelir.
    if "Kopyalama metni" in body:
        body = body.split("Kopyalama metni", 1)[1]
    body = body.replace("\t", " ")
    body = re.sub(r"[  ]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, author


_MEAL_URL = ("https://kuran.diyanet.gov.tr/mushaf/kuran-meal-2/"
             "{slug}-suresi-{sure_no}/ayet-{ayet}/diyanet-isleri-baskanligi-meali-1")


def _sure_slug(name: str) -> str:
    # "İ".lower() birleşik noktalı bir i üretir (i + U+0307); aksanları ayrıştırıp
    # birleşik işaretleri atmak hem onu hem â/î/û/ş/ğ gibi harfleri tek adımda çözer.
    slug = name.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    slug = unicodedata.normalize("NFD", slug)
    slug = "".join(c for c in slug if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


def _meal_url(meta: dict, ayet: str, fallback: str | None) -> str | None:
    """Ayete özel Diyanet meali bağlantısı.

    Kayıt düzeyindeki URL surenin 1. ayetine bakar; onu bütün ayetlere vermek
    "Nahl suresi 127. ayet" kaynağına tıklayan kullanıcıyı 1. ayete götürüyordu.
    Yönlendirmeyi yoldaki sure NUMARASI yapıyor (slug kozmetik: 'x-suresi-16' de
    'nahl-suresi-16' de aynı sayfayı açıyor), ayet numarası ise gerçekten gerekli.
    Aralıklı ayetlerde ("2-4") aralığın başı kullanılır."""
    sure_no = meta.get("sure_no")
    sure_name = meta.get("sure_name")
    if not sure_no or not sure_name or not ayet:
        return fallback
    return _MEAL_URL.format(slug=_sure_slug(str(sure_name)), sure_no=sure_no,
                            ayet=str(ayet).split("-")[0])


_SORULAR_ACILIS = re.compile(r"(?m)^\s*Değerli\s+kardeşimiz[,;:]?\s*$\n?")
_SORULAR_KAPANIS = re.compile(
    r"(?m)^\s*Selam\s+ve\s+dua\s+ile\.*\s*$\n?(^\s*Sorularla\s+İslamiyet\s*$\n?)?")


def _strip_sorular_boilerplate(text: str) -> str:
    """Her cevabın başındaki hitabı ve sonundaki imzayı at.

    ~40.000 kaydın tamamında birebir aynı olduğu için bilgi taşımıyor; kalırsa
    her vektöre gürültü olarak girer ve kısa cevaplarda sinyalin kayda değer
    bir bölümünü kaplar."""
    text = _SORULAR_ACILIS.sub("", text)
    return _SORULAR_KAPANIS.sub("", text).strip()


def _numbered_title(title: str | None, index: int, total: int) -> str | None:
    """Aynı kayıttan birden fazla pencere/chunk çıkarsa hepsi aynı başlığı taşır ->
    kaynak listesinde birbirinden ayırt edilemez görünür. Tek pencerede dokunma."""
    if not title or total <= 1:
        return title
    return f"{title} — {index + 1}. bölüm"


# Dışa aktarımdan sızan NULL göstergeleri ve "içerik yok" kalıpları. Bunlar
# anlamca boş olduğu için embedding'leri uzayın ortasına düşüyor ve her soruya
# yakın çıkıyor (ölçüldü: '\N' alakasız sorulara 0.0967 uzaklıkta, gerçek bir
# ayet 0.60+). Böyle bir parça alaka kapısını (retrieval_max_distance) geçince
# "bu soru külliyatla ilgisiz" güvencesi sessizce devre dışı kalıyor.
_PLACEHOLDER = {"\\n", "\\N", "-", "—", "…", "..."}
# dia'da madde başlığı var ama gövdesi yok olan bölümler ("AT: İslâm Öncesi.").
# Kısa ayetler sağlıklı davrandığı için bu sınır yalnızca dia'ya uygulanır.
_MIN_DIA_BODY = 30


def _anlamca_bos(content: str, source: str) -> bool:
    s = content.strip()
    if not s or s in _PLACEHOLDER:
        return True
    if source == "dia":
        # dia chunk'ları "MADDE: gövde" biçiminde yazılıyor; gövdesi yok denecek
        # kadar kısaysa parça madde başlığından ibarettir.
        body = s.split(": ", 1)[-1]
        return len(body.strip()) < _MIN_DIA_BODY
    return False


def chunk_record(source: str, rec: dict) -> list[Chunk]:
    """Kaydı chunk'lara böler ve anlamca boş olanları eler."""
    return [c for c in _chunk_record(source, rec)
            if not _anlamca_bos(c.content, source)]


def _chunk_record(source: str, rec: dict) -> list[Chunk]:
    ref_id = rec.get("id", "").split(":", 1)[-1] or rec.get("id", "")
    title = rec.get("title")
    url = rec.get("url")
    meta = rec.get("meta", {}) or {}
    text = rec.get("text", "") or ""

    if source == "fetva":
        # Soru/Cevap birimi tek parça; başlık soruyu içerir.
        return [Chunk(ref_id, 0, title, url, text.strip(),
                      {k: meta.get(k) for k in ("kategori", "alt_kategori", "tarih")})]

    if source == "sorular":
        # Cevaplar fetva'dakilerden çok daha uzun (10.000 karakteri bulabiliyor),
        # bütün bırakılamaz. Pencerelendiğinde ilk pencere dışındakiler sorusuz
        # kalıp bağlamsızlaşıyor -> soruyu her pencerenin başına yaz.
        body = _strip_sorular_boilerplate(text)
        windows = _window(body)
        return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url,
                      (f"Soru: {title}\n{w}" if title and i else w), meta)
                for i, w in enumerate(windows)]

    if source == "meal":
        # Her ayet (veya ayet-grubu) ayrı chunk; sure adı başlıkta.
        verses = meta.get("verses") or []
        sure = meta.get("sure_name") or title
        if verses:
            out = []
            for idx, v in enumerate(verses):
                ayet = v.get("ayet")
                content = f"{sure} suresi, {ayet}. ayet meali: {v.get('text','')}"
                out.append(Chunk(ref_id, idx,
                                 f"{sure} suresi {ayet}. ayet",
                                 _meal_url(meta, ayet, url), content,
                                 {"sure_no": meta.get("sure_no"), "sure_name": sure, "ayet": ayet}))
            return out
        return [Chunk(ref_id, 0, title, url, text.strip(), meta)]

    if source in ("risale", "ilmihal"):
        # Başlık, bölümün eser içindeki tam yolu ("Sözler / Onuncu Söz / ...").
        # Osmanlıca ağırlıklı metinde tek bir pencere bağlamsız kalır; dia'daki gibi
        # yolu metnin içine de yazıyoruz ki embedding hangi risalede olduğunu bilsin.
        windows = _window(text)
        return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url,
                      (f"{title}: {w}" if title else w), meta)
                for i, w in enumerate(windows)]

    if source == "dia":
        body, author = clean_dia(text)
        meta = {**meta, "author": author, "madde": title}
        windows = _window(body)
        # Aynı maddenin birden fazla penceresi aynı başlığı taşırsa (ör. mobil
        # uygulamada kaynak listesinde) birbirinden ayırt edilemez görünür ->
        # birden fazla pencere varsa bölüm no'su ekle.
        return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url,
                      (f"{title}: {w}" if title else w), meta)
                for i, w in enumerate(windows)]

    # tefsir, hadis -> pencerele
    windows = _window(text)
    return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url, w, meta)
            for i, w in enumerate(windows)]


# ---------------------------------------------------------------------------
# Merging records that repeat the same text
#
# Diyanet's Kur'an Yolu writes one commentary for an ayet RANGE and publishes it on each
# ayet's own page. The crawler walks pages, so the identical text enters the corpus once
# per ayet: measured on 2026-08-07, 9,087 of tefsir's 17,145 rows sat in such a group
# (1,904 groups, 7,183 redundant rows).
#
# That is more than wasted storage. Identical text has an identical embedding, so if one
# copy is the nearest neighbour its twins are equally near and can fill top_k by
# themselves. Over 12 questions, 4 returned duplicate text in the top three and in three
# of those all three sources were the same passage — the answer looked triply sourced.
_TEFSIR_TITLE = re.compile(
    r"^(?P<surah>.+?Suresi)\s+(?P<ayahs>\d+(?:\s*-\s*\d+)?)\.\s*Ayet Tefsiri(?P<tail>.*)$")


def _parse_tefsir_title(title: str) -> tuple[str, set[int], str] | None:
    """Split a tefsir title into (surah, ayahs it covers, trailing text)."""
    m = _TEFSIR_TITLE.match((title or "").strip())
    if not m:
        return None
    ends = [int(x) for x in re.split(r"\s*-\s*", m["ayahs"])]
    return m["surah"], set(range(ends[0], ends[-1] + 1)), m["tail"]


def _format_ayah_range(ayahs: set[int]) -> str:
    """Render an ayah set readably: {34,35,36} -> "34-36", {105,106,108} -> "105-106, 108".

    Writing a gapped set as "105-108" would claim the commentary covers 107 as well; 35 of
    the 1,904 measured groups are gapped, so this really happens."""
    ordered = sorted(ayahs)
    runs, start, previous = [], ordered[0], ordered[0]
    for n in ordered[1:] + [None]:
        if n == previous + 1:
            previous = n
            continue
        runs.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = n
    return ", ".join(runs)


def merged_title(source: str, titles: list[str]) -> str | None:
    """Fold the titles of records sharing one text into a single title.

    Only tefsir affords a meaningful merge; other sources keep their first title. Titles
    never reach search — tsv and the embedding are built from content alone — so the only
    thing this changes is the citation the reader sees."""
    if source != "tefsir" or len(titles) < 2:
        return None
    parsed = [_parse_tefsir_title(t) for t in titles]
    if not all(parsed):
        return None
    if len({p[0] for p in parsed}) != 1:
        return None
    ayahs: set[int] = set()
    for _surah, covered, _tail in parsed:
        ayahs |= covered
    surah, _, tail = parsed[0]
    return f"{surah} {_format_ayah_range(ayahs)}. Ayet Tefsiri{tail}"
