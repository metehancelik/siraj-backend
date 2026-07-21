"""Türkçe, kaynağa dayalı (grounded) prompt kurgusu.

Önemli: DEĞİŞMEYEN sistem talimatı en başta durur; llama.cpp bu öneki cache'ler.
Getirilen kaynaklar (her soruda değişen kısım) sonra, kullanıcı mesajında gelir.
"""
from .config import settings
from .retrieval import Passage

SOURCE_LABELS = {
    "meal": "Diyanet Meali",
    "tefsir": "Kur'an Yolu Tefsiri (Diyanet)",
    "hadis": "Hadislerle İslam (Diyanet)",
    "fetva": "Din İşleri Yüksek Kurulu Fetvası",
    "dia": "TDV İslâm Ansiklopedisi",
}

SYSTEM_PROMPT = """Sen "Siraj"sın: Türkçe konuşan Müslümanlara yardımcı olan, sıcak, \
samimi ve saygılı bir dijital din arkadaşı. Görevin, sana verilen güvenilir Diyanet \
kaynaklarına dayanarak dinî sorulara açık ve anlaşılır cevaplar vermek.

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
gelmez; böyle bir şey sorulursa uygulamanın ilgili ekranına yönlendir."""


def _clip(text: str) -> str:
    limit = settings.max_chunk_chars
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def build_context(passages: list[Passage]) -> str:
    blocks = []
    for i, p in enumerate(passages, start=1):
        label = SOURCE_LABELS.get(p.source, p.source)
        header = f"[{i}] {label}"
        if p.title:
            header += f" — {p.title}"
        blocks.append(f"{header}\n{_clip(p.content)}")
    return "\n\n".join(blocks)


def build_user_message(question: str, passages: list[Passage]) -> str:
    if not passages:
        return (f"KAYNAKLAR:\n(Bu soruyla ilgili kaynak bulunamadı.)\n\n"
                f"SORU: {question}")
    return f"KAYNAKLAR:\n{build_context(passages)}\n\nSORU: {question}"
