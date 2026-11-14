"""EvalDatabase mixin: batches, runs, run messages and scores."""

import json
import time
from typing import Optional


class RunsMixin:
    """Mixin for executing evaluations: batches, their runs and results.

    Expects host class (``EvalDatabase``) to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
        _update_row(table, row_id, allowed, extra_sets, **kwargs) -> None
    """

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

    def close_pending_runs(self, batch_id: int, error_text: str) -> None:
        """Mark a batch's unstarted runs as errors (runs have no
        "cancelled" status), so none is left pending forever."""
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE eval_runs SET status='error', error_text=? "
                "WHERE batch_id=? AND status='pending'",
                (error_text, batch_id),
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
        """Store a run's score of one type, replacing any earlier one.

        Re-scoring or re-judging a run (``POST /runs/{id}/score`` or
        ``/judge``) must not leave duplicate rows.
        """
        # ``with self.conn`` commits both or rolls both back.
        with self._lock, self.conn:
            self.conn.execute(
                "DELETE FROM eval_run_scores WHERE run_id=? AND score_type=?",
                (run_id, score_type),
            )
            cur = self.conn.execute(
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
