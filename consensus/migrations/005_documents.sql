-- 005_documents.sql
-- RAG document storage for discussion context

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    mime_type     TEXT NOT NULL DEFAULT 'text/plain',
    source_type   TEXT NOT NULL CHECK(source_type IN ('upload', 'url', 'text')),
    source_url    TEXT,
    markdown      TEXT NOT NULL,
    char_count    INTEGER NOT NULL DEFAULT 0,
    sections_json TEXT NOT NULL DEFAULT '[]',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discussion_documents (
    discussion_id INTEGER NOT NULL,
    document_id   INTEGER NOT NULL,
    added_at      REAL NOT NULL,
    PRIMARY KEY (discussion_id, document_id),
    FOREIGN KEY (discussion_id) REFERENCES discussions(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id)   REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index    INTEGER NOT NULL,
    content        TEXT NOT NULL,
    from_char      INTEGER NOT NULL,
    to_char        INTEGER NOT NULL,
    section_header TEXT,
    UNIQUE(document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS document_chunk_embeddings (
    chunk_id   INTEGER PRIMARY KEY
        REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding  BLOB NOT NULL
);
