from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://siraj:siraj@localhost:5432/siraj"

    # "openai" (LiteLLM/TEI's /v1), "tei" (TEI's native /embed) or "mps"
    # (local Apple Silicon GPU via sentence-transformers; only for a one-off
    # local ingest, never used in production).
    embedding_mode: str = "openai"
    # openai mode: OpenAI-compatible base (your LiteLLM), model name and key
    embedding_base_url: str = "https://ai.ravey.app/v1"
    embedding_model: str = "bge-m3"
    embedding_api_key: str = ""
    # tei mode: TEI's native address (/embed)
    embedding_url: str = "http://embeddings:80"
    embedding_dim: int = 1024
    embedding_use_e5_prefix: bool = False
    # mps mode: HF model id (same weights as TEI -> same embedding)
    embedding_local_model: str = "BAAI/bge-m3"

    llm_base_url: str = "https://ai.ravey.app/v1"
    llm_api_key: str = ""
    llm_model: str = "gemma-4-12b"

    # Conservative defaults to keep latency down on a CPU-only server
    # (the smaller the context, the faster prompt processing).
    top_k: int = 3
    candidate_k: int = 20
    max_chunk_chars: int = 550
    # If the nearest neighbour's cosine distance is above this (and there is no full-text
    # match either) the question is treated as unrelated to the corpus and retrieval returns
    # nothing. Measured: ~0.31-0.33 for relevant questions, ~0.55-0.58 for unrelated ones
    # (see chat history/2026-07-21).
    retrieval_max_distance: float = 0.42
    # Weight of the full-text leg in the hybrid fusion (the vector leg is 1.0). With equal
    # weights RRF prefers "appearing in both legs" over "being much closer in the vector
    # leg": measured (2026-08-01) on "Orucu bozan şeyler nelerdir?", the ilmihal section
    # "ORUCU BOZAN ŞEYLER" ranked below narrow fatwas. Dropping to 0.5 brought the right
    # section forward; 0.3 gives the identical result, so 0.5 is a stable point.
    fts_weight: float = 0.5
    # The vector leg is not reliable for every question: if the nearest neighbour lies
    # beyond retrieval_max_distance, semantic search is effectively not working for that
    # question, and the fixed 0.5 weight keeps the full-text leg from surfacing the right
    # answer even when it has it (RRF only looks at rank, it cannot express "this match is
    # much better"). Measured (2026-08-03) on "Abdest nasıl alınır?": the right fatwa was
    # 2nd in FTS but could not make the top 3; raising the weight to 3.0 while the vector
    # is weak brought it forward. Questions where the vector is strong (oruç, zekât,
    # teyemmüm, kurban) are not affected by this path at all.
    fts_weight_weak_vector: float = 3.0
    llm_max_tokens: int = 320
    llm_temperature: float = 0.3

    # The corpus is Turkish. Searching a non-Turkish question directly does not work:
    # measured (2026-07-31), English questions get squeezed into a 0.32-0.48 distance band,
    # relevant and unrelated do not separate ("Fransa'nın başkenti" scored 0.31, closer
    # than real questions) and the Turkish FTS never kicks in. So the question is first
    # translated into Turkish and searched that way; in the Turkish space the threshold and
    # FTS work as measured.
    translate_queries: bool = True
    # Measured, even long multi-sentence questions boil down to ~45 tokens; the limit is
    # still kept generous - generation stops at EOS, so a high ceiling costs nothing in
    # the normal case, whereas a truncated query is silently worse than an untranslated one.
    translate_max_tokens: int = 100
    translate_timeout: float = 30.0

    api_token: str = ""
    cors_origins: str = "*"


settings = Settings()
