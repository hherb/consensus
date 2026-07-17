-- Evaluation framework schema baseline
-- Separate database: evaluation.db

CREATE TABLE IF NOT EXISTS cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_key        TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    presentation    TEXT NOT NULL,
    gold_diagnosis  TEXT NOT NULL,
    difficulty      TEXT NOT NULL DEFAULT 'moderate'
        CHECK(difficulty IN ('easy', 'moderate', 'hard')),
    source          TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS case_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL,
    alias_text  TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS case_findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL,
    finding_text    TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS case_differentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL,
    diagnosis_text  TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conditions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    enable_da       INTEGER NOT NULL DEFAULT 0,
    enable_memory   INTEGER NOT NULL DEFAULT 0,
    enable_tools    INTEGER NOT NULL DEFAULT 0,
    num_rounds      INTEGER NOT NULL DEFAULT 2,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS condition_participants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    INTEGER NOT NULL,
    name            TEXT NOT NULL,
    system_prompt   TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'standard'
        CHECK(role IN ('standard', 'devils_advocate')),
    FOREIGN KEY (condition_id) REFERENCES conditions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eval_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    provider_url    TEXT NOT NULL,
    model           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'done', 'error', 'cancelled')),
    created_at      REAL NOT NULL,
    completed_at    REAL
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            INTEGER,
    case_id             INTEGER NOT NULL,
    condition_id        INTEGER NOT NULL,
    provider_url        TEXT NOT NULL,
    model               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'done', 'error')),
    conclusion          TEXT NOT NULL DEFAULT '',
    num_turns           INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    total_latency_ms    INTEGER NOT NULL DEFAULT 0,
    error_text          TEXT NOT NULL DEFAULT '',
    started_at          REAL,
    completed_at        REAL,
    FOREIGN KEY (batch_id) REFERENCES eval_batches(id) ON DELETE SET NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id),
    FOREIGN KEY (condition_id) REFERENCES conditions(id)
);

CREATE TABLE IF NOT EXISTS eval_run_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    turn_index  INTEGER NOT NULL,
    speaker     TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    model_used  TEXT NOT NULL DEFAULT '',
    tokens      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eval_run_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    score_type  TEXT NOT NULL,
    score_json  TEXT NOT NULL,
    scored_at   REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE
);
