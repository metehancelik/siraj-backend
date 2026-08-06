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
    # İngilizce arayüzde de aynı kalıplar geliyor; bunlar olmadan "thank you" gibi bir
    # mesaj tam RAG akışını tetikleyip alakasız ayet gösteriyordu.
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
_KESME_RE = re.compile(r"['’ʼ`´]")


def _fold(text: str) -> str:
    """Türkçe karakterleri ve büyük/küçük harfi normalize eder, noktalamayı atar."""
    t = text.strip().lower()
    t = t.replace("ı", "i").replace("i̇", "i")
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    # Kesme işareti kelimeyi BÖLMEZ, düşer: "Siraj'ın" -> "sirajin", "how's" -> "hows".
    # Boşluğa çevrildiğinde Türkçe ek ayrı bir kelimeye dönüşüyor ve ad tanınmıyordu;
    # aynı sebeple listedeki "hows it going" da "how's it going" yazımını kaçırıyordu.
    t = _KESME_RE.sub("", t)
    t = _WORD_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


# "Siraj ne demek?" — uygulamanın KENDİ ADININ anlamı sorulduğunda eşleşir.
# `siraj\w*` eki de yakalar: Türkçe eklemeli bir dil, kullanıcı "Sirajın", "Sirajı",
# "Siraja" yazıyor ve kelime sınırı aramak bunların hiçbirini görmüyordu.
# İkinci grup soruyu "anlam sorusu" yapan kalıp; onsuz eşleşmez, böylece "Siraj namaz
# vaktini nasıl hesaplıyor?" gibi uygulamaya dair başka sorular buraya düşmez.
_ANLAM_KALIBI = (
    r"ne demek|ne demektir|nedir|ne anlama|anlami|anlamini|manasi|manasini|"
    r"kelimesi|isminin|adinin|ismi|adi|nereden geliyor|"
    r"mean|means|meaning|stand for"
)
# Adla kalıbın ARASINA yalnızca şu kelimeler girebilir. Serbest bir `\w+` denendi ve
# "Siraj, hac nedir?" cümlesini de yakaladı — yani uygulamaya adıyla hitap edip BAŞKA bir
# şey soran kullanıcıyı, adının anlamını sormuş sayıyordu. Kapalı liste bunu önlüyor.
_ARA_KELIME = (r"isminin|ismi|adinin|adi|kelimesinin|kelimesi|sozcugunun|sozcugu|"
               r"lafzinin|lafzi|uygulamasinin|uygulamasi")
_AD_ANLAMI_RE = re.compile(
    # "siraj(ın) ne demek", "siraj isminin anlamı"
    r"\bsiraj\w*(?:\s+(?:" + _ARA_KELIME + r"))?\s+(?:" + _ANLAM_KALIBI + r")\b"
    # ters sıra: "what is the meaning of siraj"
    r"|\b(?:" + _ANLAM_KALIBI + r")(?:\s+(?:of|the|word|name|for))*\s+siraj\w*\b"
)


def is_app_name_question(text: str) -> bool:
    """Soru, uygulamanın adının ne anlama geldiğini mi soruyor?

    Neden ayrı bir yol (ölçüm 2026-08-07): korpüs kelimeyi "sirâc" yazıyor ve tam-metin
    sorgusu terimleri AND'liyor. "sirâc ne demek" -> `siraç & demek`, 7 eşleşme; ama
    "sirâcın anlamı nedir" -> `siraç & anlami & ne`, 0 eşleşme. Yani soruyu bozan şey
    "ne" gibi sıradan bir kelime. Aynı sorunun üç yazımı üç farklı kapı sonucu veriyordu
    ve açılan tek varyant `sirâc` ile ilgisiz bir vektör mesafesiyle açılıyordu — yani
    ayarlanabilir bir şey değil, gürültü.

    AND semantiğini gevşetmek yerine (ölçülüp reddedildi: 'bir' lexeme'i chunk'ların
    %76'sında geçiyor) aramayı ölçülmüş biçimde çalışan sorguya yönlendiriyoruz.
    Özel-durum yalnızca şu olgu: uygulamanın adı kaynaklarda "sirâc" yazılır. Bu ürüne
    dair bir bilgi, dine dair bir iddia değil — cevap yine yalnızca korpüsten gelir."""
    return bool(_AD_ANLAMI_RE.search(_fold(text)))


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
