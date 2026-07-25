"""Sohbet niyeti tespiti: selamlaşma/teşekkür/kısa sohbet mesajlarını yakalar.

Vektör araması "en yakın komşu" mantığıyla çalıştığı için "merhaba" gibi dinî içerik
taşımayan mesajlarda bile en yakın 3 pasajı getirir — alaka kontrolü yapmaz. Bu modül,
mesaj TAMAMEN bilinen selamlaşma/teşekkür kalıplarından oluşuyorsa retrieval'ı devre dışı
bırakmak için kullanılır. Kasıtlı olarak muhafazakâr: mesajın tamamı bilinen kalıplarla
"kapanmıyorsa" (ör. "merhaba, oruç hakkında bir sorum var") normal RAG akışı çalışır —
yanlışlıkla gerçek bir dinî soruyu sohbet sanıp reddetmek, tersinden çok daha kötüdür.
"""
import re
import unicodedata

# Uzunluktan bağımsız tam ifadeler; en uzun önce denenir ki alt dizeler yanlış eşleşmesin.
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
], key=len, reverse=True)

_WORD_RE = re.compile(r"[^a-z0-9 ]+")


def _fold(text: str) -> str:
    """Türkçe karakterleri ve büyük/küçük harfi normalize eder, noktalamayı atar."""
    t = text.strip().lower()
    t = t.replace("ı", "i").replace("i̇", "i")
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = _WORD_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_chitchat(text: str) -> bool:
    """Mesajın TAMAMEN bilinen selamlaşma/teşekkür/kısa-sohbet kalıplarından oluşup
    oluşmadığını döner. Kısmi eşleşme (ör. bir selamla başlayıp gerçek soru içeren mesaj)
    False döner — muhafazakâr davranış kasıtlıdır."""
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
