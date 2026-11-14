"""EvalDatabase mixin: seed default cases and conditions into empty tables on open."""

import logging
import time

logger = logging.getLogger(__name__)


class SeedingMixin:
    """Mixin that fills empty case/condition tables from the built-ins.

    Expects host class (``EvalDatabase``) to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
    """

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _seed_default_cases(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        if count > 0:
            return
        from consensus.evaluation.cases import CASES

        now = time.time()
        with self._lock, self.conn:
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
        logger.info("Seeded %d default evaluation cases", len(CASES))

    def _seed_default_conditions(self) -> None:
        count = self.conn.execute(
            "SELECT COUNT(*) FROM conditions"
        ).fetchone()[0]
        if count > 0:
            return
        from consensus.evaluation.conditions import CONDITIONS

        now = time.time()
        with self._lock, self.conn:
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
                        "(condition_id, name, system_prompt, role, "
                        "provider_url, model) VALUES (?,?,?,?,?,?)",
                        (cond_id, p.name, p.system_prompt, p.role,
                         getattr(p, 'provider_url', ''),
                         getattr(p, 'model', '')),
                    )
        logger.info("Seeded %d default evaluation conditions", len(CONDITIONS))
