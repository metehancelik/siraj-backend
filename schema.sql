-- Siraj RAG schema. Can be applied to an existing Postgres: psql "$DATABASE_URL" -f schema.sql
-- Requires the pgvector + unaccent extensions (the official postgres image ships unaccent;
-- for pgvector on Debian/Ubuntu: apt install postgresql-16-pgvector).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent is STABLE by default; to use it in a generated column/index we pin the
-- dictionary and define an IMMUTABLE wrapper (the standard approach).
-- Goal: a user typing "zekat/oruc/iman" matches "Zekât/oruç/îmân" in the text.
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
$$ SELECT unaccent('unaccent'::regdictionary, $1) $$;

CREATE TABLE IF NOT EXISTS chunks (
    id          bigserial PRIMARY KEY,
    source      text  NOT NULL,          -- meal | tefsir | hadis | fetva | dua | ilmihal | risale | dia
    ref_id      text  NOT NULL,          -- original id of the source record
    chunk_index int   NOT NULL DEFAULT 0,
    title       text,
    url         text,
    content     text  NOT NULL,
    meta        jsonb NOT NULL DEFAULT '{}',
    embedding   vector(1024),
    -- full-text search vector with the Turkish stemmer + accent folding
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('turkish', f_unaccent(content))) STORED,
    UNIQUE (source, ref_id, chunk_index)
);

-- Approximate nearest neighbour (cosine). Faster to build after a bulk load.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_tsv_idx
    ON chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS chunks_source_idx
    ON chunks (source);

-- ---------------------------------------------------------------------------
-- Daily cards (/v1/daily). Contract: DAILY.md
--
-- These cannot be served from `chunks`: chunks is a search corpus (windowed text +
-- embedding). Its `hadis` source is the volume text of *Hadislerle İslam*, not the short
-- saying + narrator + grade a card needs. The card corpus is seeded from the mobile app's
-- bundle: `python -m ingest.seed_daily --mobile ../siraj-mobile`.
-- ---------------------------------------------------------------------------

-- payload: the mobile HadithContent / DuaContent, as is. Matching the app's shape means
-- the remote path and the offline path produce the same model.
-- ordinal: array position in the bundle. Rotation walks this order, so both sides pick
-- the same record on the same day.
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

-- Ayah text is fetched once from alquran.cloud and kept here; the Turkish is the Diyanet
-- Vakfı meal (tr.vakfi) the app shows today, so the visible translation does not change.
-- The gain: the server hits the third party once, instead of every device every day.
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

-- Only overridden days; a field left empty falls back to rotation for that type.
-- There is no need to fill every day.
CREATE TABLE IF NOT EXISTS daily_schedule (
    date        date PRIMARY KEY,
    ayah_global int,
    hadith_id   text REFERENCES daily_hadith(id) ON DELETE SET NULL,
    dua_id      text REFERENCES daily_dua(id) ON DELETE SET NULL,
    note        text
);

-- ON DELETE SET NULL was added later; existing installs must not keep the old constraint.
-- The reason is the behaviour itself: if a record drops out of the corpus (wrong
-- attribution, content unfit for a card) the choice pinned to that day should fall back
-- to rotation on its own. The old constraint blocked seeding: since seed_daily empties and
-- rewrites the table, a single pinned record rolled back the whole corpus update, SILENTLY.
DO $$
BEGIN
    ALTER TABLE daily_schedule DROP CONSTRAINT IF EXISTS daily_schedule_hadith_id_fkey;
    ALTER TABLE daily_schedule DROP CONSTRAINT IF EXISTS daily_schedule_dua_id_fkey;
    ALTER TABLE daily_schedule
        ADD CONSTRAINT daily_schedule_hadith_id_fkey
        FOREIGN KEY (hadith_id) REFERENCES daily_hadith(id) ON DELETE SET NULL;
    ALTER TABLE daily_schedule
        ADD CONSTRAINT daily_schedule_dua_id_fkey
        FOREIGN KEY (dua_id) REFERENCES daily_dua(id) ON DELETE SET NULL;
END $$;
