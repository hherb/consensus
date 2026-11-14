"""Evaluation database — separate SQLite DB for cases, conditions, runs, and scores.

``EvalDatabase`` composes domain mixins the way ``consensus.db.Database``
does: ``eval_db_catalog`` (cases, conditions), ``eval_db_runs`` (batches,
runs, run messages, scores) and ``eval_db_seed`` (seeding empty tables). This
module keeps the connection, migrations and the shared write helpers.
"""

import os
import sqlite3
import threading
from typing import Optional

from consensus.evaluation.eval_db_catalog import CatalogMixin
from consensus.evaluation.eval_db_runs import RunsMixin
from consensus.evaluation.eval_db_seed import SeedingMixin

# Whitelist of valid table names for _update_statement
_VALID_TABLES = frozenset({
    "cases", "conditions", "eval_batches", "eval_runs",
})


def _get_migrations_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "migrations")


def _get_default_db_path() -> str:
    from consensus.config import get_data_dir
    return os.path.join(get_data_dir(), "evaluation.db")


class EvalDatabase(CatalogMixin, RunsMixin, SeedingMixin):
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
        # ``with self.conn`` rolls back a failed statement, so a refused
        # write does not keep the database write-locked.
        with self._lock, self.conn:
            return self.conn.execute(sql, params)

    @staticmethod
    def _update_statement(table: str, row_id: int, allowed: set[str],
                          extra_sets: Optional[dict] = None,
                          **kwargs: object) -> Optional[tuple[str, tuple]]:
        """The ``UPDATE`` (sql, params) for the ``allowed`` kwargs, or None.

        Pure, so a caller can run it inside its own transaction.
        """
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
        if not sets:
            return None
        vals.append(row_id)
        return f"UPDATE {table} SET {','.join(sets)} WHERE id=?", tuple(vals)

    def _update_row(self, table: str, row_id: int,
                    allowed: set[str], extra_sets: Optional[dict] = None,
                    **kwargs: object) -> None:
        """Run ``_update_statement``'s UPDATE in a transaction of its own."""
        statement = self._update_statement(table, row_id, allowed,
                                           extra_sets, **kwargs)
        if statement:
            self._execute_write(*statement)
