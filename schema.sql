-- Siraj RAG şeması. Mevcut Postgres'e uygulanabilir: psql "$DATABASE_URL" -f schema.sql
-- pgvector + unaccent eklentileri gerekir (resmi postgres imajı unaccent'i içerir;
-- pgvector için: Debian/Ubuntu apt install postgresql-16-pgvector).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent varsayılan olarak STABLE'dır; generated kolonda/indekste kullanmak için
-- sözlüğü sabitleyip IMMUTABLE bir sarmalayıcı tanımlıyoruz (standart yöntem).
-- Amaç: kullanıcı "zekat/oruc/iman" yazınca metindeki "Zekât/oruç/îmân" ile eşleşsin.
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
$$ SELECT unaccent('unaccent'::regdictionary, $1) $$;

CREATE TABLE IF NOT EXISTS chunks (
    id          bigserial PRIMARY KEY,
    source      text  NOT NULL,          -- meal | tefsir | hadis | fetva | dua | ilmihal | risale | dia
    ref_id      text  NOT NULL,          -- kaynak kaydın orijinal id'si
    chunk_index int   NOT NULL DEFAULT 0,
    title       text,
    url         text,
    content     text  NOT NULL,
    meta        jsonb NOT NULL DEFAULT '{}',
    embedding   vector(1024),
    -- Türkçe kök bulucu + aksan katlama ile tam-metin arama vektörü
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('turkish', f_unaccent(content))) STORED,
    UNIQUE (source, ref_id, chunk_index)
);

-- Yaklaşık en yakın komşu (kosinüs). Büyük veri yüklendikten sonra kurmak daha hızlıdır.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_tsv_idx
    ON chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS chunks_source_idx
    ON chunks (source);
