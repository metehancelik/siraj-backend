"""Source-aware chunking.

Each source has a different natural structure, and splitting them all with one strategy
hurts quality:
- fetva: already a short Q/A unit         -> keep whole
- sorular: Q/A, but long answers          -> window, write the question into each window
- meal : ayet/ayet-group units            -> one chunk per ayet
- tefsir: commentary on an ayet           -> whole if short, windowed if long
- hadis: huge volume texts (PDF)          -> window
- dia  : encyclopedia entries (huge)      -> window
- risale: book sections (1-148 pages)     -> window, write the section path into the text
- ilmihal: book sections (PDF)            -> window, write the section path into the text
"""
import re
import unicodedata
from dataclasses import dataclass

TARGET_CHARS = 1100   # targets ~300 tokens
OVERLAP_CHARS = 180
MAX_WHOLE = 1600      # records under this limit are kept unsplit


@dataclass
class Chunk:
    ref_id: str
    chunk_index: int
    title: str | None
    url: str | None
    content: str
    meta: dict


def _window(text: str) -> list[str]:
    """Split text into windows, respecting paragraph and sentence boundaries."""
    text = text.strip()
    if len(text) <= MAX_WHOLE:
        return [text]
    # Split into paragraphs first, then merge them up to the target size.
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(para) > TARGET_CHARS:
            # Break an overly long paragraph into sentences
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
    """Strip the boilerplate header/footer from a DIA entry; return (clean_text, author)."""
    author = None
    m = _DIA_AUTHOR.search(text)
    if m:
        author = m.group(1).strip()
    # The real body starts after the "Web Adresi:\n<url>\n" block.
    parts = text.split("Web Adresi:\n", 1)
    body = parts[1] if len(parts) == 2 else text
    body = body.split("\n", 1)[1] if "\n" in body else body  # drop the URL line
    body = _DIA_FOOTER.sub("", body)                          # drop the print-edition footer
    # The citation line ("...(date). Kopyalama metni") precedes the real body.
    if "Kopyalama metni" in body:
        body = body.split("Kopyalama metni", 1)[1]
    body = body.replace("\t", " ")
    body = re.sub(r"[  ]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, author


_MEAL_URL = ("https://kuran.diyanet.gov.tr/mushaf/kuran-meal-2/"
             "{slug}-suresi-{sure_no}/ayet-{ayet}/diyanet-isleri-baskanligi-meali-1")


def _surah_slug(name: str) -> str:
    # "İ".lower() yields an i with a combining dot (i + U+0307); decomposing accents and
    # dropping combining marks fixes that and letters like â/î/û/ş/ğ in one step.
    slug = name.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    slug = unicodedata.normalize("NFD", slug)
    slug = "".join(c for c in slug if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


def _meal_url(meta: dict, ayet: str, fallback: str | None) -> str | None:
    """Ayet-specific link to the Diyanet meal.

    The record-level URL points at the surah's first ayet; giving it to every ayet sent a
    user who clicked the "Nahl suresi 127. ayet" source to ayet 1. Routing is driven by
    the surah NUMBER in the path (the slug is cosmetic: 'x-suresi-16' and
    'nahl-suresi-16' open the same page), while the ayet number really is required.
    For ranged ayets ("2-4") the start of the range is used."""
    sure_no = meta.get("sure_no")
    sure_name = meta.get("sure_name")
    if not sure_no or not sure_name or not ayet:
        return fallback
    return _MEAL_URL.format(slug=_surah_slug(str(sure_name)), sure_no=sure_no,
                            ayet=str(ayet).split("-")[0])


_SORULAR_OPENING = re.compile(r"(?m)^\s*Değerli\s+kardeşimiz[,;:]?\s*$\n?")
_SORULAR_CLOSING = re.compile(
    r"(?m)^\s*Selam\s+ve\s+dua\s+ile\.*\s*$\n?(^\s*Sorularla\s+İslamiyet\s*$\n?)?")


def _strip_sorular_boilerplate(text: str) -> str:
    """Drop the salutation at the start and the sign-off at the end of each answer.

    They are word-for-word identical across all ~40,000 records, so they carry no
    information; left in, they add noise to every vector and take up a sizeable share of
    the signal in short answers."""
    text = _SORULAR_OPENING.sub("", text)
    return _SORULAR_CLOSING.sub("", text).strip()


def _numbered_title(title: str | None, index: int, total: int) -> str | None:
    """When one record yields several windows/chunks they all carry the same title ->
    they look indistinguishable in the source list. A single window is left alone."""
    if not title or total <= 1:
        return title
    return f"{title} - {index + 1}. bölüm"


# NULL markers leaked by the export and "no content" patterns. Being semantically
# empty, their embeddings land in the middle of the space and come out close to every
# question (measured: '\N' sat at distance 0.0967 from unrelated questions, a real ayet
# at 0.60+). Once such a chunk passes the relevance gate (retrieval_max_distance), the
# "this question is unrelated to the corpus" guarantee is silently disabled.
_PLACEHOLDER = {"\\n", "\\N", "-", "\u2014", "…", "..."}
# Sections in dia that have an entry title but no body ("AT: İslâm Öncesi.").
# Short ayets behave fine, so this limit applies to dia only.
_MIN_DIA_BODY = 30


def _is_semantically_empty(content: str, source: str) -> bool:
    s = content.strip()
    if not s or s in _PLACEHOLDER:
        return True
    if source == "dia":
        # dia chunks are written as "ENTRY: body"; if the body is short enough to be
        # practically absent, the chunk is nothing but the entry title.
        body = s.split(": ", 1)[-1]
        return len(body.strip()) < _MIN_DIA_BODY
    return False


def chunk_record(source: str, rec: dict) -> list[Chunk]:
    """Split a record into chunks and drop the semantically empty ones."""
    return [c for c in _chunk_record(source, rec)
            if not _is_semantically_empty(c.content, source)]


def _chunk_record(source: str, rec: dict) -> list[Chunk]:
    ref_id = rec.get("id", "").split(":", 1)[-1] or rec.get("id", "")
    title = rec.get("title")
    url = rec.get("url")
    meta = rec.get("meta", {}) or {}
    text = rec.get("text", "") or ""

    if source == "fetva":
        # A Q/A unit is one piece; the title contains the question.
        return [Chunk(ref_id, 0, title, url, text.strip(),
                      {k: meta.get(k) for k in ("kategori", "alt_kategori", "tarih")})]

    if source == "sorular":
        # Answers are much longer than fetva's (up to 10,000 characters), so they cannot
        # stay whole. Once windowed, every window but the first loses the question and
        # its context -> write the question at the start of each window.
        body = _strip_sorular_boilerplate(text)
        windows = _window(body)
        return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url,
                      (f"Soru: {title}\n{w}" if title and i else w), meta)
                for i, w in enumerate(windows)]

    if source == "meal":
        # Each ayet (or ayet group) is its own chunk; the surah name goes in the title.
        verses = meta.get("verses") or []
        surah = meta.get("sure_name") or title
        if verses:
            out = []
            for idx, v in enumerate(verses):
                ayet = v.get("ayet")
                content = f"{surah} suresi, {ayet}. ayet meali: {v.get('text','')}"
                out.append(Chunk(ref_id, idx,
                                 f"{surah} suresi {ayet}. ayet",
                                 _meal_url(meta, ayet, url), content,
                                 {"sure_no": meta.get("sure_no"), "sure_name": surah, "ayet": ayet}))
            return out
        return [Chunk(ref_id, 0, title, url, text.strip(), meta)]

    if source in ("risale", "ilmihal"):
        # The title is the section's full path within the work ("Sözler / Onuncu Söz / ...").
        # In Ottoman-heavy text a lone window has no context; as with dia, we also write
        # the path into the text so the embedding knows which risale it belongs to.
        windows = _window(text)
        return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url,
                      (f"{title}: {w}" if title else w), meta)
                for i, w in enumerate(windows)]

    if source == "dia":
        body, author = clean_dia(text)
        meta = {**meta, "author": author, "madde": title}
        windows = _window(body)
        # If several windows of one entry carry the same title they look
        # indistinguishable (e.g. in the mobile app's source list) -> add a part
        # number when there is more than one window.
        return [Chunk(ref_id, i, _numbered_title(title, i, len(windows)), url,
                      (f"{title}: {w}" if title else w), meta)
                for i, w in enumerate(windows)]

    # tefsir, hadis -> window
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
# of those all three sources were the same passage - the answer looked triply sourced.
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
    never reach search - tsv and the embedding are built from content alone - so the only
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
