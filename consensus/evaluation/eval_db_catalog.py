"""EvalDatabase mixin: evaluation cases and conditions CRUD."""

import time
from typing import Optional


class CatalogMixin:
    """Mixin for the cases and conditions an evaluation batch draws on.

    Expects host class (``EvalDatabase``) to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
        _update_row(table, row_id, allowed, extra_sets, **kwargs) -> None
    """

    # ------------------------------------------------------------------
    # Cases CRUD
    # ------------------------------------------------------------------

    def add_case(self, case_key: str, title: str, presentation: str,
                 gold_diagnosis: str, difficulty: str = "moderate",
                 source: str = "", aliases: list[str] | None = None,
                 findings: list[str] | None = None,
                 differentials: list[str] | None = None) -> int:
        now = time.time()
        # ``with self.conn`` commits every insert or rolls them all back.
        with self._lock, self.conn:
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
        with self._lock, self.conn:
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
        with self._lock, self.conn:
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
                    "(condition_id, name, system_prompt, role, provider_url, model) "
                    "VALUES (?,?,?,?,?,?)",
                    (cond_id, p["name"], p.get("system_prompt", ""),
                     p.get("role", "standard"), p.get("provider_url", ""),
                     p.get("model", "")),
                )
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
            with self._lock, self.conn:
                self.conn.execute(
                    "DELETE FROM condition_participants WHERE condition_id=?",
                    (condition_id,),
                )
                for p in participants:
                    self.conn.execute(
                        "INSERT INTO condition_participants "
                        "(condition_id, name, system_prompt, role, "
                        "provider_url, model) VALUES (?,?,?,?,?,?)",
                        (condition_id, p["name"], p.get("system_prompt", ""),
                         p.get("role", "standard"),
                         p.get("provider_url", ""), p.get("model", "")),
                    )

    def delete_condition(self, condition_id: int) -> None:
        self._execute_write("DELETE FROM conditions WHERE id=?", (condition_id,))
