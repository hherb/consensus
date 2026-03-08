"""Evaluation database — separate SQLite DB for cases, conditions, runs, and scores."""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Whitelist of valid table names for _update_row
_VALID_TABLES = frozenset({
    "cases", "conditions", "eval_batches", "eval_runs",
})


def _get_migrations_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "migrations")


def _get_default_db_path() -> str:
    from consensus.config import get_data_dir
    return os.path.join(get_data_dir(), "evaluation.db")


class EvalDatabase:
    """Database for evaluation framework — cases, conditions, batches, runs, scores."""

    def __init__(self, db_path: str = "") -> None:
        db_path = db_path or _get_default_db_path()
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._lock = threading.Lock()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        from consensus.migrator import run_migrations
        run_migrations(self.conn, self._lock, self.db_path,
                       migrations_dir=_get_migrations_dir())

        self._seed_default_cases()
        self._seed_default_conditions()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _update_row(self, table: str, row_id: int,
                    allowed: set[str], extra_sets: Optional[dict] = None,
                    **kwargs: object) -> None:
        if table not in _VALID_TABLES:
            raise ValueError(f"Invalid table: {table}")
        sets: list[str] = []
        vals: list[object] = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if extra_sets:
            for k, v in extra_sets.items():
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.append(row_id)
            self._execute_write(
                f"UPDATE {table} SET {','.join(sets)} WHERE id=?",
                tuple(vals),
            )

    # ------------------------------------------------------------------
    # Cases CRUD
    # ------------------------------------------------------------------

    def add_case(self, case_key: str, title: str, presentation: str,
                 gold_diagnosis: str, difficulty: str = "moderate",
                 source: str = "", aliases: list[str] | None = None,
                 findings: list[str] | None = None,
                 differentials: list[str] | None = None) -> int:
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO cases "
                "(case_key, title, presentation, gold_diagnosis, difficulty, "
                "source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (case_key, title, presentation, gold_diagnosis,
                 difficulty, source, now, now),
            )
            case_id = cur.lastrowid
            for alias in (aliases or []):
                self.conn.execute(
                    "INSERT INTO case_aliases (case_id, alias_text) VALUES (?,?)",
                    (case_id, alias),
                )
            for finding in (findings or []):
                self.conn.execute(
                    "INSERT INTO case_findings (case_id, finding_text) VALUES (?,?)",
                    (case_id, finding),
                )
            for diff in (differentials or []):
                self.conn.execute(
                    "INSERT INTO case_differentials (case_id, diagnosis_text) VALUES (?,?)",
                    (case_id, diff),
                )
            self.conn.commit()
        return case_id

    def get_case(self, case_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM cases WHERE id=?", (case_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["aliases"] = [
            r["alias_text"] for r in self.conn.execute(
                "SELECT alias_text FROM case_aliases WHERE case_id=?",
                (case_id,),
            ).fetchall()
        ]
        d["findings"] = [
            r["finding_text"] for r in self.conn.execute(
                "SELECT finding_text FROM case_findings WHERE case_id=?",
                (case_id,),
            ).fetchall()
        ]
        d["differentials"] = [
            r["diagnosis_text"] for r in self.conn.execute(
                "SELECT diagnosis_text FROM case_differentials WHERE case_id=?",
                (case_id,),
            ).fetchall()
        ]
        return d

    def get_case_by_key(self, case_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id FROM cases WHERE case_key=?", (case_key,),
        ).fetchone()
        if not row:
            return None
        return self.get_case(row["id"])

    def list_cases(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM cases ORDER BY case_key",
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            case_id = d["id"]
            d["aliases"] = [
                r["alias_text"] for r in self.conn.execute(
                    "SELECT alias_text FROM case_aliases WHERE case_id=?",
                    (case_id,),
                ).fetchall()
            ]
            d["findings"] = [
                r["finding_text"] for r in self.conn.execute(
                    "SELECT finding_text FROM case_findings WHERE case_id=?",
                    (case_id,),
                ).fetchall()
            ]
            d["differentials"] = [
                r["diagnosis_text"] for r in self.conn.execute(
                    "SELECT diagnosis_text FROM case_differentials WHERE case_id=?",
                    (case_id,),
                ).fetchall()
            ]
            result.append(d)
        return result

    def update_case(self, case_id: int, aliases: list[str] | None = None,
                    findings: list[str] | None = None,
                    differentials: list[str] | None = None,
                    **kwargs: object) -> None:
        self._update_row(
            "cases", case_id,
            allowed={"case_key", "title", "presentation", "gold_diagnosis",
                     "difficulty", "source"},
            extra_sets={"updated_at": time.time()},
            **kwargs,
        )
        # Replace child lists atomically if provided
        with self._lock:
            if aliases is not None:
                self.conn.execute(
                    "DELETE FROM case_aliases WHERE case_id=?", (case_id,))
                for alias in aliases:
                    self.conn.execute(
                        "INSERT INTO case_aliases (case_id, alias_text) VALUES (?,?)",
                        (case_id, alias),
                    )
            if findings is not None:
                self.conn.execute(
                    "DELETE FROM case_findings WHERE case_id=?", (case_id,))
                for finding in findings:
                    self.conn.execute(
                        "INSERT INTO case_findings (case_id, finding_text) VALUES (?,?)",
                        (case_id, finding),
                    )
            if differentials is not None:
                self.conn.execute(
                    "DELETE FROM case_differentials WHERE case_id=?", (case_id,))
                for diff in differentials:
                    self.conn.execute(
                        "INSERT INTO case_differentials (case_id, diagnosis_text) VALUES (?,?)",
                        (case_id, diff),
                    )
            self.conn.commit()

    def delete_case(self, case_id: int) -> None:
        self._execute_write("DELETE FROM cases WHERE id=?", (case_id,))

    # ------------------------------------------------------------------
    # Conditions CRUD
    # ------------------------------------------------------------------

    def add_condition(self, name: str, description: str = "",
                      enable_da: bool = False, enable_memory: bool = False,
                      enable_tools: bool = False, num_rounds: int = 2,
                      participants: list[dict] | None = None) -> int:
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO conditions "
                "(name, description, enable_da, enable_memory, enable_tools, "
                "num_rounds, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (name, description, int(enable_da), int(enable_memory),
                 int(enable_tools), num_rounds, now, now),
            )
            cond_id = cur.lastrowid
            for p in (participants or []):
                self.conn.execute(
                    "INSERT INTO condition_participants "
                    "(condition_id, name, system_prompt, role) VALUES (?,?,?,?)",
                    (cond_id, p["name"], p.get("system_prompt", ""),
                     p.get("role", "standard")),
                )
            self.conn.commit()
        return cond_id

    def get_condition(self, condition_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM conditions WHERE id=?", (condition_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["participants"] = [
            dict(r) for r in self.conn.execute(
                "SELECT * FROM condition_participants WHERE condition_id=? "
                "ORDER BY id",
                (condition_id,),
            ).fetchall()
        ]
        return d

    def list_conditions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM conditions ORDER BY name",
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["participants"] = [
                dict(r) for r in self.conn.execute(
                    "SELECT * FROM condition_participants "
                    "WHERE condition_id=? ORDER BY id",
                    (d["id"],),
                ).fetchall()
            ]
            result.append(d)
        return result

    def update_condition(self, condition_id: int,
                         participants: list[dict] | None = None,
                         **kwargs: object) -> None:
        # Convert bool flags to int for SQLite
        for flag in ("enable_da", "enable_memory", "enable_tools"):
            if flag in kwargs:
                kwargs[flag] = int(kwargs[flag])
        self._update_row(
            "conditions", condition_id,
            allowed={"name", "description", "enable_da", "enable_memory",
                     "enable_tools", "num_rounds"},
            extra_sets={"updated_at": time.time()},
            **kwargs,
        )
        if participants is not None:
            with self._lock:
                self.conn.execute(
                    "DELETE FROM condition_participants WHERE condition_id=?",
                    (condition_id,),
                )
                for p in participants:
                    self.conn.execute(
                        "INSERT INTO condition_participants "
                        "(condition_id, name, system_prompt, role) VALUES (?,?,?,?)",
                        (condition_id, p["name"], p.get("system_prompt", ""),
                         p.get("role", "standard")),
                    )
                self.conn.commit()

    def delete_condition(self, condition_id: int) -> None:
        self._execute_write("DELETE FROM conditions WHERE id=?", (condition_id,))

    # ------------------------------------------------------------------
    # Batches
    # ------------------------------------------------------------------

    def create_batch(self, name: str, provider_url: str, model: str) -> int:
        cur = self._execute_write(
            "INSERT INTO eval_batches "
            "(name, provider_url, model, status, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, provider_url, model, "pending", time.time()),
        )
        return cur.lastrowid

    def get_batch(self, batch_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM eval_batches WHERE id=?", (batch_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # Add run counts by status
        counts = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM eval_runs "
            "WHERE batch_id=? GROUP BY status",
            (batch_id,),
        ).fetchall()
        d["run_counts"] = {r["status"]: r["cnt"] for r in counts}
        d["total_runs"] = sum(r["cnt"] for r in counts)
        return d

    def update_batch_status(self, batch_id: int, status: str,
                            completed_at: float | None = None) -> None:
        if completed_at:
            self._execute_write(
                "UPDATE eval_batches SET status=?, completed_at=? WHERE id=?",
                (status, completed_at, batch_id),
            )
        else:
            self._execute_write(
                "UPDATE eval_batches SET status=? WHERE id=?",
                (status, batch_id),
            )

    def list_batches(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM eval_batches ORDER BY created_at DESC",
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            counts = self.conn.execute(
                "SELECT status, COUNT(*) as cnt FROM eval_runs "
                "WHERE batch_id=? GROUP BY status",
                (d["id"],),
            ).fetchall()
            d["run_counts"] = {r["status"]: r["cnt"] for r in counts}
            d["total_runs"] = sum(r["cnt"] for r in counts)
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def create_run(self, batch_id: int, case_id: int, condition_id: int,
                   provider_url: str, model: str) -> int:
        cur = self._execute_write(
            "INSERT INTO eval_runs "
            "(batch_id, case_id, condition_id, provider_url, model, status) "
            "VALUES (?,?,?,?,?,?)",
            (batch_id, case_id, condition_id, provider_url, model, "pending"),
        )
        return cur.lastrowid

    def get_run(self, run_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT r.*, c.case_key, c.title as case_title, "
            "c.gold_diagnosis, co.name as condition_name "
            "FROM eval_runs r "
            "JOIN cases c ON r.case_id = c.id "
            "JOIN conditions co ON r.condition_id = co.id "
            "WHERE r.id=?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_run(self, run_id: int, **kwargs: object) -> None:
        self._update_row(
            "eval_runs", run_id,
            allowed={"status", "conclusion", "num_turns", "total_tokens",
                     "total_latency_ms", "error_text", "started_at",
                     "completed_at"},
            **kwargs,
        )

    def list_runs(self, batch_id: int | None = None) -> list[dict]:
        if batch_id is not None:
            rows = self.conn.execute(
                "SELECT r.*, c.case_key, c.title as case_title, "
                "c.gold_diagnosis, co.name as condition_name "
                "FROM eval_runs r "
                "JOIN cases c ON r.case_id = c.id "
                "JOIN conditions co ON r.condition_id = co.id "
                "WHERE r.batch_id=? ORDER BY r.id",
                (batch_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT r.*, c.case_key, c.title as case_title, "
                "c.gold_diagnosis, co.name as condition_name "
                "FROM eval_runs r "
                "JOIN cases c ON r.case_id = c.id "
                "JOIN conditions co ON r.condition_id = co.id "
                "ORDER BY r.id",
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Run messages
    # ------------------------------------------------------------------

    def add_run_message(self, run_id: int, turn_index: int, speaker: str,
                        role: str, content: str, model_used: str = "",
                        tokens: int = 0) -> int:
        cur = self._execute_write(
            "INSERT INTO eval_run_messages "
            "(run_id, turn_index, speaker, role, content, model_used, tokens) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, turn_index, speaker, role, content, model_used, tokens),
        )
        return cur.lastrowid

    def get_run_messages(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM eval_run_messages WHERE run_id=? ORDER BY turn_index",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def add_score(self, run_id: int, score_type: str,
                  score_data: dict) -> int:
        cur = self._execute_write(
            "INSERT INTO eval_run_scores "
            "(run_id, score_type, score_json, scored_at) VALUES (?,?,?,?)",
            (run_id, score_type, json.dumps(score_data), time.time()),
        )
        return cur.lastrowid

    def get_scores(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM eval_run_scores WHERE run_id=? ORDER BY score_type",
            (run_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["score_data"] = json.loads(d["score_json"])
            except (json.JSONDecodeError, KeyError):
                d["score_data"] = {}
            result.append(d)
        return result

    def get_scores_by_batch(self, batch_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.*, r.case_id, r.condition_id, "
            "c.case_key, co.name as condition_name "
            "FROM eval_run_scores s "
            "JOIN eval_runs r ON s.run_id = r.id "
            "JOIN cases c ON r.case_id = c.id "
            "JOIN conditions co ON r.condition_id = co.id "
            "WHERE r.batch_id=? ORDER BY c.case_key, co.name, s.score_type",
            (batch_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["score_data"] = json.loads(d["score_json"])
            except (json.JSONDecodeError, KeyError):
                d["score_data"] = {}
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _seed_default_cases(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        if count > 0:
            return
        try:
            from evaluation.cases import CASES
        except ImportError:
            logger.debug("Could not import evaluation.cases for seeding")
            return

        now = time.time()
        with self._lock:
            for case in CASES:
                cur = self.conn.execute(
                    "INSERT INTO cases "
                    "(case_key, title, presentation, gold_diagnosis, "
                    "difficulty, source, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (case.id, case.title, case.presentation,
                     case.gold_diagnosis, case.difficulty, case.source,
                     now, now),
                )
                case_id = cur.lastrowid
                for alias in case.gold_aliases:
                    self.conn.execute(
                        "INSERT INTO case_aliases (case_id, alias_text) "
                        "VALUES (?,?)", (case_id, alias),
                    )
                for finding in case.key_findings:
                    self.conn.execute(
                        "INSERT INTO case_findings (case_id, finding_text) "
                        "VALUES (?,?)", (case_id, finding),
                    )
                for diff in case.differential:
                    self.conn.execute(
                        "INSERT INTO case_differentials (case_id, diagnosis_text) "
                        "VALUES (?,?)", (case_id, diff),
                    )
            self.conn.commit()
        logger.info("Seeded %d default evaluation cases", len(CASES))

    def _seed_default_conditions(self) -> None:
        count = self.conn.execute(
            "SELECT COUNT(*) FROM conditions"
        ).fetchone()[0]
        if count > 0:
            return
        try:
            from evaluation.conditions import CONDITIONS
        except ImportError:
            logger.debug("Could not import evaluation.conditions for seeding")
            return

        now = time.time()
        with self._lock:
            for cond in CONDITIONS.values():
                cur = self.conn.execute(
                    "INSERT INTO conditions "
                    "(name, description, enable_da, enable_memory, "
                    "enable_tools, num_rounds, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (cond.name, cond.description, int(cond.enable_da),
                     int(cond.enable_memory), int(cond.enable_tools),
                     cond.num_rounds, now, now),
                )
                cond_id = cur.lastrowid
                for p in cond.participants:
                    self.conn.execute(
                        "INSERT INTO condition_participants "
                        "(condition_id, name, system_prompt, role) "
                        "VALUES (?,?,?,?)",
                        (cond_id, p.name, p.system_prompt, p.role),
                    )
            self.conn.commit()
        logger.info("Seeded %d default evaluation conditions", len(CONDITIONS))
