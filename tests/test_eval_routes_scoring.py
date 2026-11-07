"""Route tests for scoring in the evaluation web UI (``eval_routes.py``).

Re-scoring and LLM judging used to be unreachable from the UI. These drive
the real aiohttp routes against a real ``EvalDatabase``, with the judge's
``AIClient.complete`` stubbed.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from consensus.evaluation.eval_routes import register_eval_routes
from tests.eval_helpers import (  # noqa: F401 — fixtures used by name
    JUDGE_SCORES,
    eval_db,
    judge_calls,
    make_run,
)

JUDGE_BODY = {"provider_url": "http://judge/v1", "model": "judge-m",
              "api_key": "k"}


@pytest.fixture
async def client(eval_db):
    """An aiohttp test client serving only the /eval/ routes."""
    app = web.Application()
    register_eval_routes(app, eval_db)
    async with TestClient(TestServer(app)) as c:
        yield c


def _types(scores: list[dict]) -> list[str]:
    return sorted(s["score_type"] for s in scores)


class TestJudgeRoute:
    async def test_judges_run_and_returns_scores(self, client, eval_db,
                                                 judge_calls):
        run_id = make_run(eval_db, conclusion="SLE")
        resp = await client.post(f"/eval/api/runs/{run_id}/judge",
                                 json=JUDGE_BODY)
        assert resp.status == 200
        scores = await resp.json()
        assert _types(scores) == ["llm_judge"]
        assert scores[0]["score_data"] == {"scores": JUDGE_SCORES, "error": ""}
        (call,) = judge_calls
        assert (call["base_url"], call["model"], call["api_key"]) == (
            "http://judge/v1", "judge-m", "k")

    async def test_api_key_is_optional(self, client, eval_db, judge_calls):
        run_id = make_run(eval_db, conclusion="SLE")
        resp = await client.post(f"/eval/api/runs/{run_id}/judge",
                                 json={"provider_url": "http://j/v1",
                                       "model": "m"})
        assert resp.status == 200
        assert judge_calls[0]["api_key"] == ""

    @pytest.mark.parametrize("missing", ["provider_url", "model"])
    async def test_requires_provider_and_model(self, client, eval_db,
                                               judge_calls, missing):
        run_id = make_run(eval_db, conclusion="SLE")
        body = {**JUDGE_BODY, missing: "  "}
        resp = await client.post(f"/eval/api/runs/{run_id}/judge", json=body)
        assert resp.status == 400
        assert "required" in (await resp.json())["error"]
        assert judge_calls == []

    async def test_unknown_run_is_404(self, client, judge_calls):
        resp = await client.post("/eval/api/runs/999/judge", json=JUDGE_BODY)
        assert resp.status == 404

    async def test_incomplete_run_is_rejected(self, client, eval_db,
                                              judge_calls):
        run_id = make_run(eval_db, status="running")
        resp = await client.post(f"/eval/api/runs/{run_id}/judge",
                                 json=JUDGE_BODY)
        assert resp.status == 400
        assert judge_calls == []

    async def test_judge_failure_is_reported_and_keeps_old_score(
            self, client, eval_db, judge_calls):
        run_id = make_run(eval_db, conclusion="SLE")
        await client.post(f"/eval/api/runs/{run_id}/judge", json=JUDGE_BODY)
        judge_calls.error = ConnectionError("judge down")
        resp = await client.post(f"/eval/api/runs/{run_id}/judge",
                                 json=JUDGE_BODY)
        assert resp.status == 502
        assert "judge down" in (await resp.json())["error"]
        (stored,) = eval_db.get_scores(run_id)
        assert stored["score_data"]["scores"] == JUDGE_SCORES


class TestRescoreRoute:
    async def test_rescoring_replaces_string_scores(self, client, eval_db):
        run_id = make_run(eval_db, conclusion="gout")
        await client.post(f"/eval/api/runs/{run_id}/score")
        eval_db.update_run(run_id, conclusion="SLE")
        resp = await client.post(f"/eval/api/runs/{run_id}/score")
        assert resp.status == 200
        scores = await resp.json()
        assert _types(scores) == ["diagnostic_accuracy", "differential_quality"]
        acc = next(s for s in scores if s["score_type"] == "diagnostic_accuracy")
        assert acc["score_data"]["correct"] is True


class TestBatchScores:
    async def test_batch_includes_its_runs_scores(self, client, eval_db):
        run_id = make_run(eval_db, conclusion="SLE")
        await client.post(f"/eval/api/runs/{run_id}/score")
        batch_id = eval_db.get_run(run_id)["batch_id"]
        resp = await client.get(f"/eval/api/batches/{batch_id}")
        batch = await resp.json()
        assert [r["id"] for r in batch["runs"]] == [run_id]
        assert _types(batch["scores"]) == ["diagnostic_accuracy",
                                           "differential_quality"]
        assert {s["run_id"] for s in batch["scores"]} == {run_id}


async def test_judge_route_reports_a_missing_case(client, eval_db, judge_calls,
                                                  monkeypatch):
    run_id = make_run(eval_db, conclusion="SLE")
    monkeypatch.setattr(eval_db, "get_case", lambda _id: None)
    resp = await client.post(f"/eval/api/runs/{run_id}/judge", json=JUDGE_BODY)
    assert resp.status == 404
    assert (await resp.json())["error"] == "Case not found"
