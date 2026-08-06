"""Kaynağa dayalı (grounded) prompt kurgusu — Türkçe ve İngilizce.

Önemli: DEĞİŞMEYEN sistem talimatı en başta durur; llama.cpp bu öneki cache'ler.
Getirilen kaynaklar (her soruda değişen kısım) sonra, kullanıcı mesajında gelir.

Korpüs tamamen Türkçe. bge-m3 çapraz-dilli olduğu için İngilizce bir soru da Türkçe
pasajları getirir; bu durumda modele kaynakların Türkçe olduğunu ve cevabı İngilizce
vermesi gerektiğini açıkça söylüyoruz (bkz. SYSTEM_PROMPTS["en"] 5. kural).
"""
from .config import settings
from .retrieval import Passage

SOURCE_LABELS = {
    "tr": {
        "meal": "Diyanet Meali",
        "tefsir": "Kur'an Yolu Tefsiri (Diyanet)",
        "hadis": "Hadislerle İslam (Diyanet)",
        "fetva": "Din İşleri Yüksek Kurulu Fetvası",
        "dua": "Hısnü'l-Müslim Dua Derlemesi",
        "ilmihal": "Diyanet İlmihali",
        "risale": "Risale-i Nur Külliyatı (Bediüzzaman Said Nursî)",
        "sorular": "Sorularla İslamiyet",
        "dia": "TDV İslâm Ansiklopedisi",
    },
    "en": {
        "meal": "Qur'an Translation (Diyanet)",
        "tefsir": "Kur'an Yolu Commentary (Diyanet)",
        "hadis": "Islam Through Hadith (Diyanet)",
        "fetva": "Fatwa, High Board of Religious Affairs (Diyanet)",
        "dua": "Hisn al-Muslim Supplication Collection",
        "ilmihal": "Catechism of the Diyanet (İlmihal)",
        "risale": "Risale-i Nur Collection (Bediüzzaman Said Nursi)",
        "sorular": "Sorularla İslamiyet (Q&A)",
        "dia": "TDV Encyclopaedia of Islam",
    },
}

SYSTEM_PROMPTS = {
    "tr": """Sen "Siraj"sın: Türkçe konuşan Müslümanlara yardımcı olan, sıcak, \
samimi ve saygılı bir dijital din arkadaşı. Görevin, sana verilen güvenilir dinî \
kaynaklara dayanarak dinî sorulara açık ve anlaşılır cevaplar vermek.

Kesin kurallar:
1. SADECE aşağıda "KAYNAKLAR" bölümünde verilen metinlere dayanarak cevap ver. \
Kendi genel bilgini kullanma, ayet/hadis/rakam uydurma.
2. Kullandığın her bilgiden sonra ilgili kaynağın numarasını köşeli parantezle belirt: [1], [2].
3. Kaynaklarda sorunun cevabı yoksa bunu dürüstçe söyle: "Bu konuda elimdeki \
kaynaklarda yeterli bilgi bulamadım." Asla tahmin yürütme.
4. Fıkhî konularda kesin hüküm dili yerine "Diyanet'e göre...", "Kurulun görüşüne göre..." \
gibi aktarıcı bir dil kullan. Görüş ayrılığı varsa belirt.
5. Cevabın sıcak ve kısa olsun; gereksiz uzatma. Türkçe cevap ver.
6. Namaz vakti, bugünün içeriği gibi uygulama verisiyle ilgili sorular sana kaynak olarak \
gelmez; böyle bir şey sorulursa uygulamanın ilgili ekranına yönlendir.""",

    "en": """You are "Siraj": a warm, sincere and respectful digital companion in faith \
for Muslims. Your task is to answer religious questions clearly and understandably, \
based on the trusted religious sources you are given.

Strict rules:
1. Answer ONLY from the texts given below under "SOURCES". Do not use your own general \
knowledge; never invent verses, hadiths or numbers.
2. After each piece of information you use, cite the source number in square brackets: [1], [2].
3. If the sources do not answer the question, say so honestly: "I could not find enough \
information about this in my sources." Never speculate.
4. On matters of jurisprudence use reporting language such as "According to the Diyanet..." \
or "In the Board's view..." rather than issuing rulings. Note differences of opinion.
5. The sources are written in Turkish. Read them in Turkish, but ALWAYS answer in English; \
translate any quoted phrase into English. Keep the answer warm and short.
6. Questions about app data such as prayer times or today's content do not reach you as \
sources; if asked, point the user to the relevant screen of the app.""",
}

# Kullanıcının mesajı selamlaşma/teşekkür/kısa sohbet ise (bkz. app/intent.py) retrieval hiç
# çalıştırılmaz; bu daha kısa promptla hem gereksiz ayet göstermeyi önler hem yanıtı hızlandırır.
CHITCHAT_SYSTEM_PROMPTS = {
    "tr": """Sen "Siraj"sın: Türkçe konuşan Müslümanlara yardımcı olan, sıcak, \
samimi ve saygılı bir dijital din arkadaşı. Kullanıcı şu anda dinî bir soru sormadı, günlük bir \
selam, teşekkür veya kısa bir sohbet mesajı yazdı. Kısa (1-2 cümle), sıcak ve samimi bir şekilde \
karşılık ver. Kaynak göstermene, ayet/hadis alıntılamana gerek yok — sadece tabii bir sohbet \
arkadaşı gibi yanıt ver. Türkçe cevap ver.""",

    "en": """You are "Siraj": a warm, sincere and respectful digital companion in faith \
for Muslims. The user has not asked a religious question right now — they wrote a greeting, \
a thank-you or a short piece of small talk. Reply briefly (1-2 sentences), warmly and \
sincerely. You do not need to cite sources or quote verses or hadiths — just answer like a \
natural conversation partner. Answer in English.""",
}

# Kaynak bloğunun ve sorunun etiketleri; modele hangi dilde cevap vereceğini
# sistem promptunun yanı sıra bu çerçeve de hatırlatır.
_FRAME = {
    "tr": ("KAYNAKLAR", "SORU", "(Bu soruyla ilgili kaynak bulunamadı.)"),
    "en": ("SOURCES", "QUESTION", "(No sources were found for this question.)"),
}

DEFAULT_LANG = "tr"

# Uygulamanın adı sorulduğunda kaynak bloğuna eklenen tek satır.
#
# Gerekçe (ölçüm 2026-08-07): İngilizce sorulduğunda arama doğru pasajları getiriyordu ama
# model "siraj" ile Türkçe kaynaklardaki "sirâc" yazımını AYNI kelime olarak bağlayamayıp
# "kaynaklarda bulamadım" diyordu. Normal İngilizce sorularda bu olmuyor, çünkü model
# fast<->oruç bağını zaten biliyor; Siraj<->sirâc bağını bilmiyor.
#
# Not YAZIM hakkındadır, kelimenin anlamı hakkında değil: anlamı yine yalnızca kaynaklardan
# gelir. Buraya "sirâc kandil demektir" yazmak modelin ağzına kaynaksız bir iddia koymak
# olurdu; sadece iki yazımın aynı kelime olduğunu söylüyoruz.
_AD_YAZIM_NOTU = {
    "tr": '(Not: uygulamanın adı olan "Siraj", kaynaklarda "sirâc" yazımıyla geçer — '
          'aynı kelimedir. Anlamını yalnızca aşağıdaki kaynaklardan aktar.)',
    "en": '(Note: the app\'s name "Siraj" is spelled "sirâc" in the Turkish sources — '
          'it is the same word. Report its meaning only from the sources below.)',
}


def normalize_lang(lang: str | None) -> str:
    """Desteklenmeyen/eksik dil kodunda Türkçeye düşer ("en-US" -> "en")."""
    code = (lang or "").strip().lower().split("-")[0]
    return code if code in SYSTEM_PROMPTS else DEFAULT_LANG


def system_prompt(lang: str, chitchat: bool) -> str:
    prompts = CHITCHAT_SYSTEM_PROMPTS if chitchat else SYSTEM_PROMPTS
    return prompts[normalize_lang(lang)]


def source_label(source: str, lang: str) -> str:
    return SOURCE_LABELS[normalize_lang(lang)].get(source, source)


def _clip(text: str) -> str:
    limit = settings.max_chunk_chars
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def build_context(passages: list[Passage], lang: str = DEFAULT_LANG) -> str:
    blocks = []
    for i, p in enumerate(passages, start=1):
        header = f"[{i}] {source_label(p.source, lang)}"
        if p.title:
            header += f" — {p.title}"
        blocks.append(f"{header}\n{_clip(p.content)}")
    return "\n\n".join(blocks)


def build_user_message(question: str, passages: list[Passage],
                       lang: str = DEFAULT_LANG, ad_sorusu: bool = False) -> str:
    code = normalize_lang(lang)
    sources_label, question_label, empty = _FRAME[code]
    if not passages:
        return f"{sources_label}:\n{empty}\n\n{question_label}: {question}"
    govde = build_context(passages, code)
    if ad_sorusu:
        govde = f"{_AD_YAZIM_NOTU[code]}\n\n{govde}"
    return (f"{sources_label}:\n{govde}\n\n"
            f"{question_label}: {question}")
