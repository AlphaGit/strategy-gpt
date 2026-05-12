-- Knowledge base storage schema (v1; SQLite stand-in for Kuzu + LanceDB).
-- All ids are stable across reingestion; reingestion purges chunks/embeddings
-- and non-Source nodes for a source, then re-emits them.

CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    title         TEXT NOT NULL,
    author        TEXT,
    year          INTEGER,
    path          TEXT NOT NULL,
    section       TEXT,
    ingested_at   TEXT NOT NULL,
    content_hash  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    page        INTEGER,
    section     TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS chunks_source_idx ON chunks(source_id, ord);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    dim         INTEGER NOT NULL,
    vec         BLOB NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    source_id   TEXT REFERENCES sources(id) ON DELETE SET NULL,
    data_json   TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE INDEX IF NOT EXISTS nodes_kind_idx ON nodes(kind);
CREATE INDEX IF NOT EXISTS nodes_source_idx ON nodes(source_id);
CREATE INDEX IF NOT EXISTS nodes_name_idx ON nodes(name);

CREATE TABLE IF NOT EXISTS edges (
    src_id              TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst_id              TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    evidence_chunk_id   TEXT REFERENCES chunks(id) ON DELETE SET NULL,
    PRIMARY KEY (src_id, dst_id, kind)
) STRICT;

CREATE INDEX IF NOT EXISTS edges_src_idx ON edges(src_id, kind);
CREATE INDEX IF NOT EXISTS edges_dst_idx ON edges(dst_id, kind);
