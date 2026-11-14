"""Characterization tests for the evaluation runner (``consensus/evaluation/``).

Both paths drive a real ``ConsensusApp`` against a temp database; the only
fake is the HTTP edge (``tests/eval_runner_helpers.py``). The CLI path is
``run_case_condition`` / ``run_evaluation`` / ``main``; the web path is
``run_case_condition_db`` (eval DB in, eval DB out).
"""

import asyncio
import json
import sys

import pytest

from consensus.evaluation import runner, runner_db
from consensus.evaluation.cases import CASES
from consensus.evaluation.conditions import (
    CONDITIONS, GENERALIST, SPECIALIST, Condition,
)
from consensus.evaluation.runner import RunResult, run_case_condition
from consensus.evaluation.runner_db import run_case_condition_db
from tests.eval_helpers import CASE_KEY, eval_db  # noqa: F401
from tests.eval_runner_helpers import (  # noqa: F401
    TOKENS_PER_CALL, is_summary, is_synthesis, is_turn, llm,
)

URL = "http://fake/v1"
CASE = CASES[0]
PAIR = Condition(name="pair", description="two agents",
                 participants=[GENERALIST, SPECIALIST], num_rounds=1)


def turn_calls(fake) -> list[dict]:
    """The recorded participant-turn requests."""
    return [c for c in fake.calls if is_turn({"messages": c["messages"]})]


# ---------------------------------------------------------------------------
# CLI path: run_case_condition
# ---------------------------------------------------------------------------

class TestRunCaseCondition:
    async def test_runs_every_turn_and_records_the_synthesis(self, llm):
        result = await run_case_condition(CASE, CONDITIONS["multi_agent"],
                                          URL, "m1")
        assert result.error == ""
        assert result.num_turns == 6
        assert [m["speaker"] for m in result.messages] == [
            "Dr. Generalist", "Dr. Specialist", "Dr. Pharmacologist"] * 2
        assert result.total_tokens == 6 * TOKENS_PER_CALL
        assert result.conclusion.startswith("## Final Synthesis")
        assert all(c["base_url"] == URL and c["model"] == "m1"
                   for c in llm.calls)
        first = result.messages[0]
        assert first["role"] == "standard" and first["model"] == "m1"
        assert first["tokens"] == TOKENS_PER_CALL and first["content"]

    async def test_topic_carries_the_case_presentation(self, llm):
        await run_case_condition(CASE, PAIR, URL, "m1")
        assert CASE.presentation in json.dumps(llm.calls[0]["messages"])

    async def test_api_key_comes_from_the_named_env_var(self, llm, monkeypatch):
        monkeypatch.setenv("EVAL_TEST_KEY", "sk-cli")
        await run_case_condition(CASE, PAIR, URL, "m1", "EVAL_TEST_KEY")
        assert {c["api_key"] for c in llm.calls} == {"sk-cli"}

    async def test_num_rounds_override(self, llm):
        result = await run_case_condition(CASE, PAIR, URL, "m1",
                                          num_rounds_override=2)
        assert result.num_turns == 4

    async def test_tools_condition_offers_web_search(self, llm):
        cond = Condition(name="t", description="", participants=[GENERALIST],
                         enable_tools=True, num_rounds=1)
        await run_case_condition(CASE, cond, URL, "m1")
        names = {t["function"]["name"] for t in turn_calls(llm)[0]["tools"]}
        assert "web_search" in names

    async def test_turn_failure_is_recorded(self, llm):
        llm.fail_when = is_turn
        result = await run_case_condition(CASE, PAIR, URL, "m1")
        assert result.error
        assert result.num_turns == 0

    async def test_failed_summary_stops_the_run(self, llm):
        llm.fail_when = is_summary
        result = await run_case_condition(CASE, PAIR, URL, "m1")
        assert result.num_turns == 0
        assert len(result.messages) == 1
        assert result.error.startswith("Turn completion failed:")

    async def test_turn_and_synthesis_failures_are_both_kept(self, llm):
        llm.fail_when = lambda payload: not is_summary(payload)
        result = await run_case_condition(CASE, PAIR, URL, "m1")
        first, second = result.error.split("; ")
        assert "Conclusion failed:" not in first
        assert second.startswith("Conclusion failed:")

    async def test_failed_synthesis_is_an_error(self, llm):
        llm.fail_when = is_synthesis
        result = await run_case_condition(CASE, PAIR, URL, "m1")
        assert result.error.startswith("Conclusion failed:")

    async def test_start_error_is_recorded(self, llm, monkeypatch):
        monkeypatch.setattr(runner.ConsensusApp, "start_discussion",
                            lambda self, **kw: {"error": "no go"})
        result = await run_case_condition(CASE, PAIR, URL, "m1")
        assert result.error == "no go"
        assert llm.calls == []

    async def test_unexpected_exception_is_recorded(self, llm, monkeypatch):
        def boom(self, *a, **kw):
            raise RuntimeError("kaput")
        monkeypatch.setattr(runner.ConsensusApp, "set_topic", boom)
        result = await run_case_condition(CASE, PAIR, URL, "m1")
        assert result.error == "kaput"

    def test_to_dict(self):
        result = RunResult(CASE, PAIR)
        d = result.to_dict()
        assert d["case_id"] == CASE.id and d["condition"] == "pair"
        assert d["gold_diagnosis"] == CASE.gold_diagnosis
        assert set(d) >= {"conclusion", "num_turns", "total_tokens",
                          "total_latency_ms", "error", "timestamp", "messages"}


# ---------------------------------------------------------------------------
# CLI path: run_evaluation and main
# ---------------------------------------------------------------------------

class TestRunEvaluation:
    async def test_writes_one_file_per_run_and_a_combined_file(
            self, llm, tmp_path):
        results = await runner.run_evaluation(
            CASES[:2], [PAIR], URL, "m1", output_dir=str(tmp_path))
        assert len(results) == 2
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == sorted([f"{CASES[0].id}_pair.json",
                                f"{CASES[1].id}_pair.json",
                                "all_results.json"])
        combined = json.loads((tmp_path / "all_results.json").read_text())
        assert [r["case_id"] for r in combined] == [c.id for c in CASES[:2]]


class TestDefaultOutputDir:
    async def test_defaults_to_the_data_dir(self, llm, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "get_data_dir", lambda: str(tmp_path))
        await runner.run_evaluation([CASE], [PAIR], URL, "m1")
        assert (tmp_path / runner.RESULTS_DIRNAME / "all_results.json").exists()


class TestMain:
    def run_main(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["runner", *argv])
        runner.main()

    def test_parse_args_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("EVAL_PROVIDER_URL", "http://env/v1")
        monkeypatch.setenv("EVAL_MODEL", "env-model")
        monkeypatch.setattr(sys, "argv", ["runner"])
        args = runner.parse_args()
        assert args.provider_url == "http://env/v1"
        assert args.model == "env-model"
        assert args.num_rounds is None and not args.dry_run

    def test_dry_run_prints_the_plan(self, monkeypatch, capsys):
        self.run_main(monkeypatch, "--dry-run", "--cases", CASE.id,
                      "--conditions", "baseline", "--num-rounds", "3")
        out = capsys.readouterr().out
        assert CASE.id in out and "baseline" in out
        assert "Total runs: 1" in out and "Rounds override: 3" in out

    @pytest.mark.parametrize("flag", ["--cases", "--conditions"])
    def test_unknown_selection_exits(self, monkeypatch, flag):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, flag, "nope")
        assert exc.value.code == 1

    def test_runs_and_summarises(self, monkeypatch, capsys, tmp_path):
        seen = {}

        async def fake_run_evaluation(**kwargs):
            seen.update(kwargs)
            bad = RunResult(CASE, PAIR)
            bad.error = "it broke"
            good = RunResult(CASE, PAIR)
            good.total_tokens = 1234
            return [bad, good]

        monkeypatch.setattr(runner, "run_evaluation", fake_run_evaluation)
        self.run_main(monkeypatch, "--cases", CASE.id, "--output",
                      str(tmp_path))
        out = capsys.readouterr().out
        assert seen["cases"] == [CASE]
        assert seen["conditions"] == list(CONDITIONS.values())
        assert seen["output_dir"] == str(tmp_path)
        assert "Errors: 1" in out and "it broke" in out
        assert "Successful: 1" in out and "1,234" in out


# ---------------------------------------------------------------------------
# Web path: run_case_condition_db
# ---------------------------------------------------------------------------

def make_db_run(db, *, participants: list[dict], num_rounds: int = 1,
                enable_tools: bool = False, model: str = "m1") -> int:
    """Create a pending run of the seeded case under a new condition."""
    case = db.get_case_by_key(CASE_KEY)
    cond_id = db.add_condition(f"c{len(db.list_conditions())}",
                               num_rounds=num_rounds,
                               enable_tools=enable_tools,
                               participants=participants)
    batch_id = db.create_batch("b", URL, model)
    return db.create_run(batch_id, case["id"], cond_id, URL, model)


TWO = [{"name": "A", "system_prompt": "You are A."},
       {"name": "B", "system_prompt": "You are B.", "role": "devils_advocate"}]


class TestRunCaseConditionDb:
    async def test_unknown_run_raises(self, eval_db):
        with pytest.raises(ValueError, match="not found"):
            await run_case_condition_db(eval_db, 999)

    async def test_missing_condition_raises(self, eval_db, monkeypatch):
        run_id = make_db_run(eval_db, participants=TWO)
        monkeypatch.setattr(eval_db, "get_condition", lambda cid: None)
        with pytest.raises(ValueError, match="Case or condition"):
            await run_case_condition_db(eval_db, run_id)

    async def test_single_participant_is_one_direct_call(self, eval_db, llm):
        run_id = make_db_run(eval_db, participants=[TWO[0]])
        await run_case_condition_db(eval_db, run_id, api_key="sk-1")
        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["api_key"] == "sk-1" and call["model"] == "m1"
        assert call["messages"][0] == {"role": "system",
                                       "content": "You are A."}
        run = eval_db.get_run(run_id)
        assert run["status"] == "done" and run["num_turns"] == 1
        assert run["conclusion"] == "Reply 1"
        assert run["total_tokens"] == TOKENS_PER_CALL
        assert run["started_at"] and run["completed_at"]
        msgs = eval_db.get_run_messages(run_id)
        assert [(m["speaker"], m["content"]) for m in msgs] == [("A", "Reply 1")]

    async def test_single_participant_overrides_provider_and_model(
            self, eval_db, llm):
        p = {**TWO[0], "provider_url": "http://other/v1", "model": "pm",
             "system_prompt": ""}
        run_id = make_db_run(eval_db, participants=[p])
        await run_case_condition_db(eval_db, run_id)
        call = llm.calls[0]
        assert call["base_url"] == "http://other/v1" and call["model"] == "pm"
        assert call["messages"][0]["role"] == "user"

    async def test_single_participant_failure_marks_run_error(
            self, eval_db, llm):
        llm.fail_when = lambda payload: True
        run_id = make_db_run(eval_db, participants=[TWO[0]])
        await run_case_condition_db(eval_db, run_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "error" and run["error_text"]
        assert run["completed_at"]

    async def test_multi_party_records_turns_and_synthesis(self, eval_db, llm):
        run_id = make_db_run(eval_db, participants=TWO, num_rounds=2)
        await run_case_condition_db(eval_db, run_id, api_key="sk-2")
        run = eval_db.get_run(run_id)
        assert run["status"] == "done" and run["num_turns"] == 4
        assert run["total_tokens"] == 4 * TOKENS_PER_CALL
        assert run["conclusion"].startswith("## Final Synthesis")
        msgs = eval_db.get_run_messages(run_id)
        assert [m["turn_index"] for m in msgs] == [0, 1, 2, 3]
        assert [(m["speaker"], m["role"]) for m in msgs[:2]] == [
            ("A", "standard"), ("B", "devils_advocate")]
        assert {c["api_key"] for c in llm.calls} == {"sk-2"}

    async def test_num_rounds_override(self, eval_db, llm):
        run_id = make_db_run(eval_db, participants=TWO, num_rounds=1)
        await run_case_condition_db(eval_db, run_id, num_rounds_override=2)
        assert eval_db.get_run(run_id)["num_turns"] == 4

    async def test_one_participant_with_rounds_is_a_discussion(
            self, eval_db, llm):
        run_id = make_db_run(eval_db, participants=[TWO[0]], num_rounds=2)
        await run_case_condition_db(eval_db, run_id)
        assert eval_db.get_run(run_id)["num_turns"] == 2

    async def test_per_participant_provider_and_model(self, eval_db, llm):
        parts = [{"name": "A", "provider_url": "http://pa/v1", "model": "ma"},
                 {"name": "B", "provider_url": "http://pa/v1"},
                 {"name": "C", "model": "mc"}]
        run_id = make_db_run(eval_db, participants=parts)
        await run_case_condition_db(eval_db, run_id, api_key="sk-3")
        turns = [(c["base_url"], c["model"]) for c in turn_calls(llm)]
        assert turns == [("http://pa/v1", "ma"), ("http://pa/v1", "m1"),
                         (URL, "mc")]
        assert {c["api_key"] for c in llm.calls} == {"sk-3"}

    async def test_tools_condition_offers_web_search(self, eval_db, llm):
        run_id = make_db_run(eval_db, participants=[TWO[0]], num_rounds=2,
                             enable_tools=True)
        await run_case_condition_db(eval_db, run_id)
        names = {t["function"]["name"] for t in turn_calls(llm)[0]["tools"]}
        assert "web_search" in names

    async def test_turn_failure_marks_run_error(self, eval_db, llm):
        llm.fail_when = is_turn
        run_id = make_db_run(eval_db, participants=TWO)
        await run_case_condition_db(eval_db, run_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "error" and run["error_text"]
        assert run["num_turns"] == 0 and run["completed_at"]

    async def test_summary_failure_marks_run_error(self, eval_db, llm):
        llm.fail_when = is_summary
        run_id = make_db_run(eval_db, participants=TWO)
        await run_case_condition_db(eval_db, run_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "error"
        assert run["error_text"].startswith("Turn completion failed:")
        assert len(eval_db.get_run_messages(run_id)) == 1

    async def test_synthesis_failure_marks_run_error(self, eval_db, llm):
        llm.fail_when = is_synthesis
        run_id = make_db_run(eval_db, participants=TWO)
        await run_case_condition_db(eval_db, run_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "error"
        assert run["error_text"].startswith("Conclusion failed:")
        assert run["num_turns"] == 2

    async def test_concurrent_runs_keep_their_own_keys(self, eval_db, llm):
        llm.yield_each_call = True
        run_a = make_db_run(eval_db, participants=TWO, model="ma")
        run_b = make_db_run(eval_db, participants=TWO, model="mb")
        await asyncio.gather(
            run_case_condition_db(eval_db, run_a, api_key="sk-a"),
            run_case_condition_db(eval_db, run_b, api_key="sk-b"))
        keys = {(c["model"], c["api_key"]) for c in llm.calls}
        assert keys == {("ma", "sk-a"), ("mb", "sk-b")}

    async def test_callers_byok_keys_do_not_reach_the_run(self, eval_db, llm):
        # The batch task inherits the request's BYOK keys, which are keyed
        # by the main app's provider ids, not the temp app's.
        runner_db.ConsensusApp.set_request_api_keys(
            {str(i): "sk-leak" for i in range(1, 100)})
        try:
            run_id = make_db_run(eval_db, participants=TWO)
            await run_case_condition_db(eval_db, run_id)
        finally:
            runner_db.ConsensusApp.clear_request_api_keys()
        assert {c["api_key"] for c in llm.calls} == {""}

    async def test_start_error_marks_run_error(self, eval_db, llm,
                                               monkeypatch):
        monkeypatch.setattr(runner_db.ConsensusApp, "start_discussion",
                            lambda self, **kw: {"error": "no go"})
        run_id = make_db_run(eval_db, participants=TWO)
        await run_case_condition_db(eval_db, run_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "error" and run["error_text"] == "no go"
        assert run["completed_at"]

    async def test_unexpected_exception_marks_run_error(
            self, eval_db, llm, monkeypatch):
        def boom(self, *a, **kw):
            raise RuntimeError("kaput")
        monkeypatch.setattr(runner_db.ConsensusApp, "set_topic", boom)
        run_id = make_db_run(eval_db, participants=TWO)
        await run_case_condition_db(eval_db, run_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "error" and run["error_text"] == "kaput"

