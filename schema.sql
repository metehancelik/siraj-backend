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

-- ---------------------------------------------------------------------------
-- Günün kartları (/v1/daily). Sözleşme: DAILY.md
--
-- Bunlar `chunks` üzerinden karşılanamaz: chunks bir arama korpüsüdür (pencerelenmiş
-- metin + embedding). Oradaki `hadis` kaynağı *Hadislerle İslam* cilt metnidir, kartın
-- istediği kısa söz + ravi + derece değil. Kart korpüsü mobil uygulamanın paketinden
-- tohumlanır: `python -m ingest.seed_daily --mobile ../siraj-mobile`.
-- ---------------------------------------------------------------------------

-- payload: mobildeki HadithContent / DuaContent, olduğu gibi. Uygulama ile aynı şekil
-- olması, uzak yol ile çevrimdışı yolun aynı modeli üretmesi demek.
-- ordinal: paketteki dizi sırası. Rotasyon buna göre yürüdüğü için iki taraf aynı gün
-- aynı kaydı seçer.
CREATE TABLE IF NOT EXISTS daily_hadith (
    id      text  PRIMARY KEY,
    ordinal int   NOT NULL UNIQUE,
    payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_dua (
    id      text  PRIMARY KEY,
    ordinal int   NOT NULL UNIQUE,
    payload jsonb NOT NULL
);

-- Ayet metni alquran.cloud'dan bir kez alınıp burada durur; Türkçesi uygulamanın bugün
-- gösterdiği Diyanet Vakfı meali (tr.vakfi), yani görünen çeviri değişmiyor. Kazanç,
-- üçüncü tarafa her cihazın her gün değil sunucunun bir kez gitmesi.
CREATE TABLE IF NOT EXISTS ayah_text (
    global_number   int  PRIMARY KEY,
    surah_number    int  NOT NULL,
    number_in_surah int  NOT NULL,
    surah_name_ar   text NOT NULL,
    surah_name_en   text NOT NULL,
    arabic          text NOT NULL,
    translation_tr  text NOT NULL,
    translation_en  text,
    fetched_at      timestamptz NOT NULL DEFAULT now()
);

-- Yalnızca müdahale edilen günler; boş bırakılan alan o tür için rotasyona düşer.
-- Her günü doldurmak gerekmez.
CREATE TABLE IF NOT EXISTS daily_schedule (
    date        date PRIMARY KEY,
    ayah_global int,
    hadith_id   text REFERENCES daily_hadith(id),
    dua_id      text REFERENCES daily_dua(id),
    note        text
);
