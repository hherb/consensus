"""Characterization tests for ``EvalDatabase`` (``consensus/evaluation/``).

Written against a real temp-file SQLite database before the #61 split of
``eval_db.py``, so they go through the public ``EvalDatabase`` API only and
stay valid whichever module defines each method.
"""

import sqlite3

import pytest

from consensus.evaluation.cases import CASES
from consensus.evaluation.conditions import CONDITIONS
from consensus.evaluation.eval_db import EvalDatabase
from tests.eval_helpers import CASE_KEY, eval_db, make_run  # noqa: F401

PARTICIPANT = {"name": "A", "system_prompt": "s", "role": "devils_advocate",
               "provider_url": "http://p/v1", "model": "pm"}


def _case(db: EvalDatabase, **overrides) -> int:
    fields = {"case_key": "k-1", "title": "T", "presentation": "P",
              "gold_diagnosis": "G", **overrides}
    return db.add_case(**fields)


def _count(db: EvalDatabase, table: str) -> int:
    return db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestConstruction:
    def test_seeds_default_cases_and_conditions(self, eval_db):
        assert len(eval_db.list_cases()) == len(CASES)
        assert len(eval_db.list_conditions()) == len(CONDITIONS)
        sle = eval_db.get_case_by_key(CASE_KEY)
        assert sle["aliases"] and sle["findings"] and sle["differentials"]

    def test_reopening_does_not_reseed(self, tmp_path):
        path = str(tmp_path / "e.db")
        EvalDatabase(path).conn.close()
        db = EvalDatabase(path)
        assert len(db.list_cases()) == len(CASES)
        assert len(db.list_conditions()) == len(CONDITIONS)
        db.conn.close()

    def test_creates_parent_dir(self, tmp_path):
        db = EvalDatabase(str(tmp_path / "a" / "b" / "e.db"))
        assert (tmp_path / "a" / "b" / "e.db").exists()
        db.conn.close()

    def test_default_path_is_in_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("consensus.config.get_data_dir",
                            lambda: str(tmp_path))
        db = EvalDatabase()
        assert db.db_path == str(tmp_path / "evaluation.db")
        db.conn.close()

    def test_missing_seed_modules_leave_tables_empty(self, tmp_path,
                                                     monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "consensus.evaluation.cases", None)
        monkeypatch.setitem(sys.modules, "consensus.evaluation.conditions",
                            None)
        db = EvalDatabase(str(tmp_path / "e.db"))
        assert db.list_cases() == [] and db.list_conditions() == []
        db.conn.close()


class TestCases:
    def test_add_and_get_round_trip(self, eval_db):
        case_id = _case(eval_db, difficulty="hard", source="src",
                        aliases=["a1", "a2"], findings=["f"],
                        differentials=["d"])
        case = eval_db.get_case(case_id)
        assert (case["case_key"], case["difficulty"], case["source"]) == (
            "k-1", "hard", "src")
        assert (case["aliases"], case["findings"], case["differentials"]) \
            == (["a1", "a2"], ["f"], ["d"])
        assert case["created_at"] == case["updated_at"]

    def test_unknown_ids_and_keys_are_none(self, eval_db):
        assert eval_db.get_case(999) is None
        assert eval_db.get_case_by_key("nope") is None

    def test_list_is_ordered_by_key_with_children(self, eval_db):
        _case(eval_db, case_key="aaa", aliases=["x"])
        cases = eval_db.list_cases()
        assert [c["case_key"] for c in cases] == sorted(
            c["case_key"] for c in cases)
        assert cases[0]["case_key"] == "aaa"
        assert cases[0]["aliases"] == ["x"]
        assert cases[0]["findings"] == [] == cases[0]["differentials"]

    def test_update_scalar_fields_and_replace_lists(self, eval_db):
        case_id = _case(eval_db, aliases=["old"], findings=["old"],
                        differentials=["old"])
        eval_db.update_case(case_id, title="New", aliases=["n1"],
                            findings=[], differentials=["n2", "n3"],
                            ignored="x")
        case = eval_db.get_case(case_id)
        assert case["title"] == "New"
        assert (case["aliases"], case["findings"], case["differentials"]) \
            == (["n1"], [], ["n2", "n3"])
        assert case["updated_at"] >= case["created_at"]

    def test_update_without_lists_keeps_them(self, eval_db):
        case_id = _case(eval_db, aliases=["keep"])
        eval_db.update_case(case_id, source="s")
        assert eval_db.get_case(case_id)["aliases"] == ["keep"]

    def test_delete_cascades_children(self, eval_db):
        case_id = _case(eval_db, aliases=["a"])
        before = _count(eval_db, "case_aliases")
        eval_db.delete_case(case_id)
        assert eval_db.get_case(case_id) is None
        assert _count(eval_db, "case_aliases") == before - 1

    def test_failed_add_leaves_no_partial_case(self, eval_db):
        """An insert failing after the case row must not leave that row
        pending for the next commit to persist."""
        with pytest.raises(sqlite3.Error):
            _case(eval_db, aliases=[["not", "text"]])
        eval_db.create_batch("b", "u", "m")  # any later commit
        assert eval_db.get_case_by_key("k-1") is None

    def test_failed_update_leaves_lists_unchanged(self, eval_db):
        case_id = _case(eval_db, aliases=["a"], findings=["f"])
        with pytest.raises(sqlite3.Error):
            eval_db.update_case(case_id, aliases=["b"],
                                findings=[["not", "text"]])
        eval_db.create_batch("b", "u", "m")
        case = eval_db.get_case(case_id)
        assert (case["aliases"], case["findings"]) == (["a"], ["f"])


class TestConditions:
    def test_add_and_get_round_trip(self, eval_db):
        cond_id = eval_db.add_condition(
            "c", description="d", enable_da=True, enable_tools=True,
            num_rounds=3, participants=[PARTICIPANT, {"name": "B"}])
        cond = eval_db.get_condition(cond_id)
        assert (cond["enable_da"], cond["enable_memory"],
                cond["enable_tools"], cond["num_rounds"]) == (1, 0, 1, 3)
        a, b = cond["participants"]
        assert {k: a[k] for k in PARTICIPANT} == PARTICIPANT
        assert (b["system_prompt"], b["role"], b["provider_url"],
                b["model"]) == ("", "standard", "", "")

    def test_unknown_id_is_none(self, eval_db):
        assert eval_db.get_condition(999) is None

    def test_list_is_ordered_by_name_with_participants(self, eval_db):
        eval_db.add_condition("000-first", participants=[PARTICIPANT])
        conds = eval_db.list_conditions()
        assert [c["name"] for c in conds] == sorted(c["name"] for c in conds)
        assert conds[0]["participants"][0]["name"] == "A"

    def test_update_converts_flags_and_replaces_participants(self, eval_db):
        cond_id = eval_db.add_condition("c", participants=[{"name": "old"}])
        eval_db.update_condition(cond_id, enable_memory=True, num_rounds=4,
                                 participants=[PARTICIPANT])
        cond = eval_db.get_condition(cond_id)
        assert (cond["enable_memory"], cond["num_rounds"]) == (1, 4)
        (p,) = cond["participants"]
        assert {k: p[k] for k in PARTICIPANT} == PARTICIPANT

    def test_update_without_participants_keeps_them(self, eval_db):
        cond_id = eval_db.add_condition("c", participants=[PARTICIPANT])
        eval_db.update_condition(cond_id, description="new")
        assert eval_db.get_condition(cond_id)["participants"][0]["model"] \
            == "pm"

    def test_failed_add_leaves_no_partial_condition(self, eval_db):
        with pytest.raises(sqlite3.Error):
            eval_db.add_condition("c", participants=[{"name": "A",
                                                      "role": "judge"}])
        eval_db.create_batch("b", "u", "m")
        assert "c" not in [c["name"] for c in eval_db.list_conditions()]

    def test_failed_update_keeps_old_participants(self, eval_db):
        cond_id = eval_db.add_condition("c", participants=[PARTICIPANT])
        with pytest.raises(sqlite3.Error):
            eval_db.update_condition(cond_id, participants=[
                {"name": "B"}, {"name": "C", "role": "judge"}])
        eval_db.create_batch("b", "u", "m")
        names = [p["name"] for p in
                 eval_db.get_condition(cond_id)["participants"]]
        assert names == ["A"]

    def test_delete(self, eval_db):
        cond_id = eval_db.add_condition("c")
        eval_db.delete_condition(cond_id)
        assert eval_db.get_condition(cond_id) is None


class TestUpdateRow:
    def test_rejects_unknown_table(self, eval_db):
        with pytest.raises(ValueError, match="Invalid table"):
            eval_db._update_row("users", 1, allowed={"x"}, x=1)

    def test_no_allowed_fields_is_a_no_op(self, eval_db):
        run_id = make_run(eval_db, status="running")
        eval_db.update_run(run_id, case_id=999)
        assert eval_db.get_run(run_id)["status"] == "running"


class TestBatchesAndRuns:
    def test_batch_counts_runs_by_status(self, eval_db):
        run_id = make_run(eval_db, status="done")
        batch_id = eval_db.get_run(run_id)["batch_id"]
        case_id = eval_db.get_run(run_id)["case_id"]
        cond_id = eval_db.get_run(run_id)["condition_id"]
        eval_db.create_run(batch_id, case_id, cond_id, "u", "m")
        batch = eval_db.get_batch(batch_id)
        assert batch["status"] == "pending"
        assert batch["run_counts"] == {"done": 1, "pending": 1}
        assert batch["total_runs"] == 2
        (listed,) = eval_db.list_batches()
        assert listed["run_counts"] == batch["run_counts"]

    def test_batches_list_newest_first(self, eval_db):
        first = eval_db.create_batch("one", "u", "m")
        second = eval_db.create_batch("two", "u", "m")
        eval_db.conn.execute("UPDATE eval_batches SET created_at=1 "
                             "WHERE id=?", (first,))
        assert [b["id"] for b in eval_db.list_batches()] == [second, first]

    def test_unknown_batch_and_run_are_none(self, eval_db):
        assert eval_db.get_batch(999) is None
        assert eval_db.get_run(999) is None

    def test_batch_status_with_and_without_completion(self, eval_db):
        batch_id = eval_db.create_batch("b", "u", "m")
        eval_db.update_batch_status(batch_id, "running")
        assert eval_db.get_batch(batch_id)["completed_at"] is None
        eval_db.update_batch_status(batch_id, "done", 123.0)
        batch = eval_db.get_batch(batch_id)
        assert (batch["status"], batch["completed_at"]) == ("done", 123.0)

    def test_run_joins_case_and_condition(self, eval_db):
        run_id = make_run(eval_db, conclusion="c")
        run = eval_db.get_run(run_id)
        assert run["case_key"] == CASE_KEY and run["conclusion"] == "c"
        assert run["condition_name"] == eval_db.list_conditions()[0]["name"]
        assert run["gold_diagnosis"] and run["case_title"]

    def test_list_runs_all_or_by_batch(self, eval_db):
        a = make_run(eval_db)
        b = make_run(eval_db)
        assert [r["id"] for r in eval_db.list_runs()] == [a, b]
        batch_b = eval_db.get_run(b)["batch_id"]
        assert [r["id"] for r in eval_db.list_runs(batch_b)] == [b]

    def test_run_messages_in_turn_order(self, eval_db):
        run_id = make_run(eval_db)
        eval_db.add_run_message(run_id, 1, "B", "participant", "second")
        eval_db.add_run_message(run_id, 0, "A", "moderator", "first",
                                model_used="m", tokens=7)
        msgs = eval_db.get_run_messages(run_id)
        assert [m["content"] for m in msgs] == ["first", "second"]
        assert (msgs[0]["model_used"], msgs[0]["tokens"]) == ("m", 7)


class TestScores:
    def test_add_replaces_same_type_only(self, eval_db):
        run_id = make_run(eval_db)
        eval_db.add_score(run_id, "string_match", {"v": 1})
        eval_db.add_score(run_id, "llm_judge", {"v": 2})
        eval_db.add_score(run_id, "string_match", {"v": 3})
        scores = eval_db.get_scores(run_id)
        assert [(s["score_type"], s["score_data"]) for s in scores] == [
            ("llm_judge", {"v": 2}), ("string_match", {"v": 3})]

    def test_scores_by_batch_join_case_and_condition(self, eval_db):
        run_id = make_run(eval_db)
        eval_db.add_score(run_id, "string_match", {"v": 1})
        batch_id = eval_db.get_run(run_id)["batch_id"]
        (s,) = eval_db.get_scores_by_batch(batch_id)
        assert (s["case_key"], s["score_data"]) == (CASE_KEY, {"v": 1})
        assert s["condition_name"] and s["case_id"] and s["condition_id"]

    def test_malformed_score_json_reads_as_empty(self, eval_db):
        run_id = make_run(eval_db)
        eval_db.add_score(run_id, "string_match", {"v": 1})
        eval_db.conn.execute("UPDATE eval_run_scores SET score_json='{'")
        batch_id = eval_db.get_run(run_id)["batch_id"]
        assert eval_db.get_scores(run_id)[0]["score_data"] == {}
        assert eval_db.get_scores_by_batch(batch_id)[0]["score_data"] == {}
