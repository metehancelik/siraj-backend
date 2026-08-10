"""Chat-intent detection: catches greetings, thanks and other small talk.

Vector search works by nearest neighbour, so it returns the closest three passages even
for a message like "merhaba" that carries no religious content — it never checks
relevance. This module exists to switch retrieval off when a message consists ENTIRELY of
known greeting/thanks/small-talk phrases. It is deliberately conservative: if the message
does not "close" on known phrases (e.g. "merhaba, oruç hakkında bir sorum var") the normal
RAG path runs, because mistaking a real religious question for small talk is far worse
than the reverse.
"""
import re
import unicodedata

# Whole phrases regardless of length; the longest is tried first so substrings cannot
# match by accident.
_FILLER_PHRASES = sorted([
    "selamun aleykum", "esselamu aleykum", "aleykum selam", "selamlar", "selam", "merhabalar",
    "merhaba", "mrb", "hey", "hi", "hello",
    "gunaydin", "iyi gunler", "iyi aksamlar", "iyi geceler", "iyi haftalar", "iyi hafta sonlari",
    "nasilsin", "nasilsiniz", "naber", "naberler", "ne haber", "ne var ne yok",
    "iyi misin", "iyi misiniz", "nerdesin", "nerelerdesin", "ne yapiyorsun",
    "iyiyim", "iyiyim sen nasilsin", "iyiyim siz nasilsiniz", "ben de iyiyim",
    "fena degil", "fena degilim", "cok iyiyim", "iyidir", "idare eder", "soyle boyle",
    "tesekkurler", "tesekkur ederim", "cok tesekkur ederim", "tesekkur ederiz",
    "sagol", "sagolun", "sagolasin", "eyvallah", "elinize saglik", "allah razi olsun",
    "hoscakal", "hoscakalin", "gorusuruz", "bay bay", "baybay", "kendine iyi bak",
    "kimsin", "sen kimsin", "adin ne", "ismin ne", "nesin sen", "sen nesin", "seni kim yapti",
    "tamam", "tamamdir", "peki", "anladim", "ok", "okey", "super", "harika", "guzel", "nice",
    "evet", "hayir", "olur", "olur mu",
    # The English interface receives the same openers; without these a message like
    # "thank you" triggered the full RAG path and surfaced an unrelated verse.
    "assalamu alaikum", "asalamu alaikum", "salam alaikum", "walaikum salam", "salam",
    "good morning", "good afternoon", "good evening", "good night",
    "how are you", "how are you doing", "hows it going", "how is it going",
    "whats up", "i am fine", "im fine", "im good", "i am good", "not bad",
    "thanks", "thank you", "thanks a lot", "thank you so much", "many thanks",
    "much appreciated", "appreciate it", "god bless you", "bless you",
    "bye", "goodbye", "see you", "see you later", "take care",
    "who are you", "what are you", "whats your name", "what is your name",
    "yes", "no", "sure", "alright", "got it", "understood", "great", "awesome", "cool",
], key=len, reverse=True)

_WORD_RE = re.compile(r"[^a-z0-9 ]+")
_APOSTROPHE_RE = re.compile(r"['’ʼ`´]")


def _fold(text: str) -> str:
    """Normalise Turkish characters and case, and drop punctuation."""
    t = text.strip().lower()
    t = t.replace("ı", "i").replace("i̇", "i")
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    # An apostrophe does NOT split a word, it disappears: "Siraj'ın" -> "sirajin",
    # "how's" -> "hows". Turned into a space, a Turkish suffix became a word of its own
    # and the name went unrecognised; for the same reason "hows it going" in the list
    # above could never match the spelling "how's it going".
    t = _APOSTROPHE_RE.sub("", t)
    t = _WORD_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


# Matches when the user asks what the app's OWN NAME means.
# `siraj\w*` also catches the suffix: Turkish is agglutinative and people write "Sirajın",
# "Sirajı", "Siraja", none of which a word boundary saw.
_MEANING_PATTERN = (
    r"ne demek|ne demektir|nedir|ne anlama|anlami|anlamini|manasi|manasini|"
    r"kelimesi|isminin|adinin|ismi|adi|nereden geliyor|"
    r"mean|means|meaning|stand for"
)
# Only these words may sit BETWEEN the name and the pattern. A free `\w+` was tried and it
# also matched "Siraj, hac nedir?" — a user addressing the app by name while asking about
# something else entirely. The closed list prevents that.
_INFIX_WORD = (r"isminin|ismi|adinin|adi|kelimesinin|kelimesi|sozcugunun|sozcugu|"
               r"lafzinin|lafzi|uygulamasinin|uygulamasi")
_APP_NAME_QUESTION_RE = re.compile(
    # "siraj(ın) ne demek", "siraj isminin anlamı"
    r"\bsiraj\w*(?:\s+(?:" + _INFIX_WORD + r"))?\s+(?:" + _MEANING_PATTERN + r")\b"
    # reversed: "what is the meaning of siraj"
    r"|\b(?:" + _MEANING_PATTERN + r")(?:\s+(?:of|the|word|name|for))*\s+siraj\w*\b"
)


def is_app_name_question(text: str) -> bool:
    """Is the question asking what the app's name means?

    Why this needs its own path (measured 2026-08-07): the corpus spells the word "sirâc"
    and the full-text query ANDs its terms. "sirâc ne demek" becomes `siraç & demek` and
    matches 7 chunks, but "sirâcın anlamı nedir" becomes `siraç & anlami & ne` and matches
    none — an ordinary word like "ne" is what breaks it. Three spellings of one question
    gave three different gate outcomes, and the only one that opened did so on a vector
    distance unrelated to sirâc. That is noise, not something to tune.

    Rather than loosen the AND semantics (measured and rejected: the 'bir' lexeme appears
    in 76% of chunks) the search is routed to the phrasing that is measured to work. The
    only thing special-cased is that the app's name is spelled "sirâc" in the sources: a
    fact about the product, not a claim about religion. The answer still comes solely from
    the corpus.
    """
    return bool(_APP_NAME_QUESTION_RE.search(_fold(text)))


def is_chitchat(text: str) -> bool:
    """Whether the message consists ENTIRELY of known greeting/thanks/small-talk phrases.

    A partial match (e.g. an opener followed by a real question) returns False; the
    conservative behaviour is deliberate."""
    remaining = _fold(text)
    if not remaining:
        return False
    changed = True
    while changed and remaining:
        changed = False
        for phrase in _FILLER_PHRASES:
            if remaining == phrase:
                remaining = ""
                changed = True
                break
            if remaining.startswith(phrase + " "):
                remaining = remaining[len(phrase) + 1:]
                changed = True
                break
            if remaining.endswith(" " + phrase):
                remaining = remaining[: -(len(phrase) + 1)]
                changed = True
                break
    return remaining == ""
