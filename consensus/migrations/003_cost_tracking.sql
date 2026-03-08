CREATE TABLE IF NOT EXISTS model_pricing (
    model_id TEXT PRIMARY KEY,
    prompt_cost REAL NOT NULL DEFAULT 0,
    completion_cost REAL NOT NULL DEFAULT 0,
    last_updated REAL NOT NULL DEFAULT 0
);

ALTER TABLE messages ADD COLUMN cost REAL;
