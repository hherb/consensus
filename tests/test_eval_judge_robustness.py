"""Regression tests for LLM-judge and score-storage failure modes (PR #108).

A failed or malformed judgement must be reported as a failure and must not
replace a run's earlier good ``llm_judge`` score; re-scoring one run must
not touch another's rows. The judge is stubbed at ``AIClient.complete``.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from consensus.evaluation.constants import JUDGE_DIMENSIONS
from consensus.evaluation.scorer import (
    LLM_JUDGE_PROMPT,
    llm_judge_run_from_db,
    llm_judge_score,
)
from tests.eval_helpers import (  # noqa: F401 — fixtures used by name
    JUDGE_SCORES,
    eval_db,
    judge_calls,
    make_run,
)

EVAL_SCORING_JS = (Path(__file__).parent.parent / "consensus" / "evaluation"
                   / "static" / "eval_scoring.js")


def _judge_row(db, run_id: int) -> dict:
    (row,) = [s for s in db.get_scores(run_id) if s["score_type"] == "llm_judge"]
    return row["score_data"]


class TestMessagelessExceptions:
    """httpx/asyncio timeouts stringify to "" — still a failure."""

    @pytest.mark.parametrize("error", [httpx.ConnectTimeout(""),
                                       asyncio.TimeoutError()])
    async def test_error_text_is_never_blank(self, judge_calls, error):
        judge_calls.error = error
        r = await llm_judge_score("p", "g", "c", "http://j/v1", "m")
        assert r["scores"] == {} and r["error"]

    async def test_timeout_keeps_earlier_score(self, eval_db, judge_calls):
        run_id = make_run(eval_db, conclusion="SLE")
        await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m")
        judge_calls.error = httpx.ConnectTimeout("")
        result = await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m")
        assert result["error"]
        assert _judge_row(eval_db, run_id) == {"scores": JUDGE_SCORES,
                                               "error": ""}


class TestJudgeReplyValidation:
    @pytest.mark.parametrize("reply", [
        {"comments": "no scores at all"},
        {**JUDGE_SCORES, "accuracy": "4"},
        {**JUDGE_SCORES, "accuracy": 9},
        {**JUDGE_SCORES, "accuracy": 0},
        {**JUDGE_SCORES, "accuracy": True},
        {k: v for k, v in JUDGE_SCORES.items() if k != "evidence"},
        [1, 2, 3],
        4,
    ])
    async def test_invalid_reply_is_a_failure(self, judge_calls, reply):
        judge_calls.reply = json.dumps(reply)
        r = await llm_judge_score("p", "g", "c", "http://j/v1", "m")
        assert r["scores"] == {} and r["error"]

    async def test_fractional_score_in_range_is_accepted(self, judge_calls):
        reply = {**JUDGE_SCORES, "accuracy": 3.5}
        judge_calls.reply = json.dumps(reply)
        r = await llm_judge_score("p", "g", "c", "http://j/v1", "m")
        assert r == {"scores": reply, "error": ""}

    async def test_invalid_reply_keeps_earlier_score(self, eval_db,
                                                     judge_calls):
        run_id = make_run(eval_db, conclusion="SLE")
        await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m")
        judge_calls.reply = json.dumps({"comments": "forgot the scores"})
        result = await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m")
        assert result["error"]
        assert _judge_row(eval_db, run_id)["scores"] == JUDGE_SCORES


class TestJudgeDimensionsInStep:
    """The prompt, the Python constant and the web UI must agree."""

    @pytest.mark.parametrize("dim", JUDGE_DIMENSIONS)
    def test_prompt_asks_for_dimension(self, dim):
        assert f'"{dim}": N' in LLM_JUDGE_PROMPT

    def test_web_ui_uses_same_dimensions(self):
        js = EVAL_SCORING_JS.read_text()
        expected = ", ".join(f"'{d}'" for d in JUDGE_DIMENSIONS)
        assert f"const JUDGE_DIMENSIONS = [{expected}];" in js


class TestAddScoreIsolation:
    def test_replacing_one_run_leaves_other_runs_alone(self, eval_db):
        a = make_run(eval_db)
        b = make_run(eval_db)
        eval_db.add_score(a, "diagnostic_accuracy", {"v": "a1"})
        eval_db.add_score(b, "diagnostic_accuracy", {"v": "b1"})
        eval_db.add_score(a, "diagnostic_accuracy", {"v": "a2"})
        assert [s["score_data"] for s in eval_db.get_scores(a)] == [{"v": "a2"}]
        assert [s["score_data"] for s in eval_db.get_scores(b)] == [{"v": "b1"}]
