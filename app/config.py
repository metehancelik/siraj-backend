from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://siraj:siraj@localhost:5432/siraj"

    # "openai" (LiteLLM/TEI'nin /v1) veya "tei" (TEI'nin yerel /embed)
    embedding_mode: str = "openai"
    # openai modu: OpenAI-uyumlu taban (LiteLLM'iniz), model adı ve anahtar
    embedding_base_url: str = "https://ai.ravey.app/v1"
    embedding_model: str = "bge-m3"
    embedding_api_key: str = ""
    # tei modu: TEI'nin yerel adresi (/embed)
    embedding_url: str = "http://embeddings:80"
    embedding_dim: int = 1024
    embedding_use_e5_prefix: bool = False

    llm_base_url: str = "https://ai.ravey.app/v1"
    llm_api_key: str = ""
    llm_model: str = "gemma-4-12b"

    # CPU-only sunucu için gecikmeyi düşürmek amacıyla muhafazakâr varsayılanlar
    # (bağlam ne kadar küçükse prompt işleme o kadar hızlı).
    top_k: int = 3
    candidate_k: int = 20
    max_chunk_chars: int = 550
    llm_max_tokens: int = 320
    llm_temperature: float = 0.3

    api_token: str = ""
    cors_origins: str = "*"


settings = Settings()
