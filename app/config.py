from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://siraj:siraj@localhost:5432/siraj"

    # "openai" (LiteLLM/TEI'nin /v1), "tei" (TEI'nin yerel /embed) veya "mps"
    # (sentence-transformers ile yerel Apple Silicon GPU; sadece tek seferlik
    # lokal ingest için, üretimde kullanılmaz).
    embedding_mode: str = "openai"
    # openai modu: OpenAI-uyumlu taban (LiteLLM'iniz), model adı ve anahtar
    embedding_base_url: str = "https://ai.ravey.app/v1"
    embedding_model: str = "bge-m3"
    embedding_api_key: str = ""
    # tei modu: TEI'nin yerel adresi (/embed)
    embedding_url: str = "http://embeddings:80"
    embedding_dim: int = 1024
    embedding_use_e5_prefix: bool = False
    # mps modu: HF model id (TEI ile aynı ağırlıklar -> aynı embedding)
    embedding_local_model: str = "BAAI/bge-m3"

    llm_base_url: str = "https://ai.ravey.app/v1"
    llm_api_key: str = ""
    llm_model: str = "gemma-4-12b"

    # CPU-only sunucu için gecikmeyi düşürmek amacıyla muhafazakâr varsayılanlar
    # (bağlam ne kadar küçükse prompt işleme o kadar hızlı).
    top_k: int = 3
    candidate_k: int = 20
    max_chunk_chars: int = 550
    # En yakın komşunun kosinüs mesafesi bunun üzerindeyse (ve tam-metin eşleşmesi de
    # yoksa) soru bu külliyatla alakasız sayılır, retrieval boş döner. Ölçüldü: alakalı
    # sorularda ~0.31-0.33, alakasızlarda ~0.55-0.58 (bkz. sohbet geçmişi/2026-07-21).
    retrieval_max_distance: float = 0.42
    # Hibrit füzyonda tam-metin ayağının ağırlığı (vektör ayağı 1.0). Eşit ağırlıkta
    # RRF, "iki ayakta birden görünme"yi "vektörde çok daha yakın olma"ya tercih ediyor:
    # ölçüldüğünde (2026-08-01) "Orucu bozan şeyler nelerdir?" sorusunda ilmihal'in
    # "ORUCU BOZAN ŞEYLER" bölümü dar fetvaların altında kalıyordu. 0.5'e düşürünce
    # doğru bölüm öne geldi; 0.3 ile sonuç birebir aynı, yani 0.5 kararlı bir nokta.
    fts_weight: float = 0.5
    # Vektör ayağı her soruda güvenilir değil: en yakın komşu retrieval_max_distance'ın
    # ötesindeyse o soruda anlamsal arama fiilen çalışmıyor demektir ve sabit 0.5 ağırlık,
    # tam-metin ayağının doğru cevabı taşıdığı hâlde yüzeye çıkmasını engelliyor (RRF
    # yalnızca sıraya bakar, "bu eşleşme çok daha iyi"yi ifade edemez). Ölçüldüğünde
    # (2026-08-03) "Abdest nasıl alınır?" sorusunda doğru fetva FTS'te 2. sıradaydı ama
    # ilk 3'e giremiyordu; vektör zayıfken ağırlığı 3.0'a çıkarmak onu öne aldı. Vektörün
    # güçlü olduğu sorular (oruç, zekât, teyemmüm, kurban) bu yoldan hiç etkilenmiyor.
    fts_weight_weak_vector: float = 3.0
    llm_max_tokens: int = 320
    llm_temperature: float = 0.3

    # Korpüs Türkçe. Türkçe olmayan bir soruyu doğrudan aratmak çalışmıyor: ölçüldüğünde
    # (2026-07-31) İngilizce sorularda mesafe 0.32-0.48 bandında sıkışıyor, alakalı ile
    # alakasız ayrışmıyor ("Fransa'nın başkenti" 0.31 alıp gerçek sorulardan yakın çıktı)
    # ve Türkçe FTS hiç devreye girmiyor. Bu yüzden soru önce Türkçeye çevrilip öyle
    # aranıyor; Türkçe uzayda eşik ve FTS ölçülmüş haliyle çalışır.
    translate_queries: bool = True
    # Ölçüldüğünde uzun/çok cümleli sorular bile ~45 token'a sadeleşiyor; sınır yine de
    # geniş tutuldu — üretim EOS'ta durduğu için yüksek tavan normal durumda maliyetsiz,
    # buna karşılık kırpılmış bir sorgu çevrilmemiş sorgudan sessizce daha kötüdür.
    translate_max_tokens: int = 100
    translate_timeout: float = 30.0

    api_token: str = ""
    cors_origins: str = "*"


settings = Settings()
