-- Contract: schema of the `faces` database.
-- Mirrors src/face_index/schema.sql, the single source; tests/integration/test_schema.py fails if the two differ.
-- This copy exists so reviewers and the search-script plan can read the schema without opening code.
-- Idempotent: applied on every run by `init_schema`.
-- Consumers: the image indexer (read/write), face_matcher.py (read-only, see index-read.md).

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;  -- types and operators stay in public, on every search_path

DO $$ BEGIN
    CREATE TYPE image_status AS ENUM ('pending', 'indexed', 'no_faces', 'skipped');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE skip_reason AS ENUM ('unsupported_format', 'too_small', 'too_large');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS model_profile (
    id                  smallserial PRIMARY KEY,
    key                 text        NOT NULL UNIQUE,
    detector            text        NOT NULL,
    model               text        NOT NULL,
    dimensions          smallint    NOT NULL CHECK (dimensions = 512),
    distance            text        NOT NULL,
    align               boolean     NOT NULL,
    normalization       text        NOT NULL,
    min_face_confidence real        NOT NULL,
    deepface_version    text        NOT NULL,
    tensorflow_version  text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS page (
    id               bigserial   PRIMARY KEY,
    url              text        NOT NULL UNIQUE,
    site             text        NOT NULL CHECK (site IN ('cnn', 'people')),
    depth            smallint    NOT NULL CHECK (depth BETWEEN 0 AND 10),
    first_crawled_at timestamptz NOT NULL DEFAULT now(),
    last_crawled_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS image (
    id              bigserial    PRIMARY KEY,
    sha256          bytea        NOT NULL UNIQUE CHECK (octet_length(sha256) = 32),
    status          image_status NOT NULL,
    skip_reason     skip_reason,
    original_format text,
    original_width  integer,
    original_height integer,
    width           integer,
    height          integer,
    jpeg            bytea,
    detect_attempts smallint     NOT NULL DEFAULT 0 CHECK (detect_attempts BETWEEN 0 AND 3),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    indexed_at      timestamptz,
    CONSTRAINT skip_reason_iff_skipped CHECK ((status = 'skipped') = (skip_reason IS NOT NULL)),
    CONSTRAINT jpeg_iff_kept CHECK ((status IN ('pending', 'indexed')) = (jpeg IS NOT NULL)),
    CONSTRAINT dims_pair CHECK ((width IS NULL) = (height IS NULL)),
    CONSTRAINT original_dims_pair CHECK ((original_width IS NULL) = (original_height IS NULL)),
    CONSTRAINT dims_iff_analyzed CHECK ((status IN ('pending', 'indexed', 'no_faces')) = (width IS NOT NULL)),
    CONSTRAINT max_side CHECK (width IS NULL OR (width BETWEEN 1 AND 1024 AND height BETWEEN 1 AND 1024)),
    CONSTRAINT indexed_at_iff_done CHECK ((status IN ('indexed', 'no_faces')) = (indexed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS image_pending_idx ON image (id) WHERE status = 'pending' AND detect_attempts < 3;

CREATE TABLE IF NOT EXISTS image_url (
    url           text        PRIMARY KEY,
    image_id      bigint      NOT NULL REFERENCES image (id) ON DELETE CASCADE,
    first_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS page_image (
    page_id       bigint      NOT NULL REFERENCES page (id) ON DELETE CASCADE,
    image_id      bigint      NOT NULL REFERENCES image (id) ON DELETE CASCADE,
    is_lead       boolean     NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (page_id, image_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS page_image_one_lead ON page_image (page_id) WHERE is_lead;
CREATE INDEX IF NOT EXISTS page_image_image_idx ON page_image (image_id);

CREATE TABLE IF NOT EXISTS face (
    id               bigserial   PRIMARY KEY,
    image_id         bigint      NOT NULL REFERENCES image (id) ON DELETE CASCADE,
    face_index       smallint    NOT NULL CHECK (face_index >= 0),
    x                integer     NOT NULL CHECK (x >= 0),
    y                integer     NOT NULL CHECK (y >= 0),
    w                integer     NOT NULL CHECK (w > 0),
    h                integer     NOT NULL CHECK (h > 0),
    confidence       real        NOT NULL,
    embedding        vector(512) NOT NULL,
    model_profile_id smallint    NOT NULL REFERENCES model_profile (id),
    UNIQUE (image_id, face_index)
);

CREATE INDEX IF NOT EXISTS face_embedding_hnsw ON face USING hnsw (embedding vector_cosine_ops);

-- Shared per-host request pacing (research R4). Operational state, not index data.
CREATE TABLE IF NOT EXISTS host_pace (
    host      text        NOT NULL,
    kind      text        NOT NULL CHECK (kind IN ('page', 'image')),
    next_slot timestamptz NOT NULL,
    PRIMARY KEY (host, kind)
);

-- Failure history per link; enforces the 3-attempt cap (FR-023a). Deleted on success.
CREATE TABLE IF NOT EXISTS fetch_attempt (
    url             text        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('page', 'image')),
    attempts        smallint    NOT NULL CHECK (attempts BETWEEN 1 AND 3),
    last_error      text        NOT NULL,
    last_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_run_id     text        NOT NULL,
    PRIMARY KEY (url, kind)
);
