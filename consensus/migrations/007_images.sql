-- 007_images.sql
-- Image storage for discussion visual context

CREATE TABLE IF NOT EXISTS images (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    filename           TEXT NOT NULL,
    original_filename  TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    mime_type          TEXT NOT NULL DEFAULT 'image/png',
    width              INTEGER,
    height             INTEGER,
    file_size          INTEGER NOT NULL DEFAULT 0,
    storage_path       TEXT NOT NULL,
    source_type        TEXT NOT NULL CHECK(source_type IN ('upload', 'url', 'ai_generated')),
    source_url         TEXT,
    uploader_entity_id INTEGER,
    created_at         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discussion_images (
    discussion_id INTEGER NOT NULL,
    image_id      INTEGER NOT NULL,
    added_at      REAL NOT NULL,
    PRIMARY KEY (discussion_id, image_id),
    FOREIGN KEY (discussion_id) REFERENCES discussions(id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS message_images (
    message_id INTEGER NOT NULL,
    image_id   INTEGER NOT NULL,
    PRIMARY KEY (message_id, image_id),
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
);
