-- Frontpage cloud store (Neon Postgres).
-- Mixed CN/EN content: use pg_trgm (trigram) for search instead of a
-- language-specific tsvector config, so substring search works for both.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS stories (
    story_id        integer PRIMARY KEY,   -- = aggregated-article id (clusters.id), stable
    title           text NOT NULL,
    category        text NOT NULL DEFAULT 'uncategorized',
    category_label  text,
    tier            text,
    dek             text,
    image_url       text,
    published_at    timestamptz,
    source_count    integer NOT NULL DEFAULT 1,
    synthesis_md    text,
    synthesis_en    text,
    synthesis_model text,
    search_text     text NOT NULL DEFAULT '',
    sources         jsonb NOT NULL DEFAULT '[]'::jsonb,
    tags            jsonb NOT NULL DEFAULT '[]'::jsonb,
    like_count      integer NOT NULL DEFAULT 0,
    active          boolean NOT NULL DEFAULT true,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 兼容已建的旧表(CREATE TABLE IF NOT EXISTS 不会给旧表补列)
ALTER TABLE stories ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE stories ADD COLUMN IF NOT EXISTS synthesis_en text;

CREATE INDEX IF NOT EXISTS stories_search_trgm
    ON stories USING gin (search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS stories_title_trgm
    ON stories USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS stories_published_idx
    ON stories (published_at DESC);
CREATE INDEX IF NOT EXISTS stories_category_idx
    ON stories (category);
