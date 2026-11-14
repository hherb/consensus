"""Batch execution: ``runner_db.execute_batch`` and the cancel route.

``execute_batch`` runs each pending run through ``runner_db`` and then
auto-scores it; these drive it with only the HTTP edge faked (and, for
cancellation, a run that blocks until cancelled).
"""

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from consensus.evaluation import eval_routes, runner_db, scorer
from consensus.evaluation.constants import AUTO_SCORE_FAILED_PREFIX
from consensus.evaluation.eval_routes import register_eval_routes
from consensus.evaluation.runner_db import execute_batch
from tests.eval_helpers import CASE_KEY, eval_db  # noqa: F401
from tests.eval_runner_helpers import llm  # noqa: F401

URL = "http://fake/v1"
ONE = [{"name": "A", "system_prompt": "You are A."}]
JSON = {"Content-Type": "application/json"}


def make_batch(db, n_runs: int = 1) -> tuple[int, list[int]]:
    """A batch of ``n_runs`` single-participant runs; (batch id, run ids)."""
    cond_id = db.add_condition("one", num_rounds=1, participants=ONE)
    cases = db.list_cases()[:n_runs]
    batch_id = db.create_batch("b", URL, "m1")
    return batch_id, [db.create_run(batch_id, c["id"], cond_id, URL, "m1")
                      for c in cases]


@pytest.fixture
async def client(eval_db):
    """An aiohttp test client serving only the /eval/ routes."""
    app = web.Application()
    register_eval_routes(app, eval_db)
    async with TestClient(TestServer(app)) as c:
        yield c


class TestExecuteBatch:
    async def test_runs_and_scores_every_pending_run(self, eval_db, llm):
        batch_id, (run_id,) = make_batch(eval_db)
        await execute_batch(eval_db, batch_id, api_key="sk-b")
        run = eval_db.get_run(run_id)
        assert run["status"] == "done", run["error_text"]
        assert eval_db.get_scores(run_id)
        assert eval_db.get_batch(batch_id)["status"] == "done"
        assert llm.calls[0]["api_key"] == "sk-b"

    async def test_scoring_failure_keeps_the_run_done(
            self, eval_db, llm, monkeypatch, client):
        """#114: the run finished; only its scoring failed."""
        def broken(db, run_id):
            raise RuntimeError("scorer broke")
        monkeypatch.setattr(scorer, "score_run_from_db", broken)
        batch_id, (run_id,) = make_batch(eval_db)
        await execute_batch(eval_db, batch_id)
        run = eval_db.get_run(run_id)
        assert run["status"] == "done"
        assert run["conclusion"] == "Reply 1"
        assert run["error_text"].startswith(AUTO_SCORE_FAILED_PREFIX)
        assert "scorer broke" in run["error_text"]
        assert eval_db.get_batch(batch_id)["status"] == "done"

        monkeypatch.undo()
        resp = await client.post(f"/eval/api/runs/{run_id}/score",
                                 json={}, headers=JSON)
        assert resp.status == 200, await resp.text()
        assert eval_db.get_run(run_id)["error_text"] == ""


class TestCancelBatch:
    async def test_cancel_stamps_the_current_run_and_closes_the_rest(
            self, eval_db, client, monkeypatch):
        """#113: no run is left pending or running after a cancel."""
        started = asyncio.Event()

        async def blocking_run(eval_db, run_id, api_key=""):
            eval_db.update_run(run_id, status="running")
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runner_db, "run_case_condition_db", blocking_run)
        cond_id = eval_db.add_condition("one", num_rounds=1, participants=ONE)
        case_ids = [c["id"] for c in eval_db.list_cases()[:3]]
        resp = await client.post("/eval/api/batches/run", headers=JSON, json={
            "provider_url": URL, "model": "m1", "api_key": "",
            "case_ids": case_ids, "condition_ids": [cond_id]})
        assert resp.status == 201, await resp.text()
        batch_id = (await resp.json())["id"]
        task = eval_routes._active_batch_tasks[batch_id]
        await asyncio.wait_for(started.wait(), 5)

        resp = await client.post(f"/eval/api/batches/{batch_id}/cancel",
                                 json={}, headers=JSON)
        assert resp.status == 200, await resp.text()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)

        first, *rest = eval_db.list_runs(batch_id)
        assert first["status"] == "error" and first["error_text"] == "Cancelled"
        assert first["completed_at"]
        assert len(rest) == 2
        for run in rest:
            assert run["status"] == "error"
            assert run["error_text"] == "Cancelled before it started"
        assert eval_db.get_batch(batch_id)["status"] == "cancelled"

    async def test_cancel_without_a_live_task_closes_pending_runs(
            self, eval_db, client):
        """A batch left 'running' by a restart has no task to cancel."""
        batch_id, run_ids = make_batch(eval_db, n_runs=2)
        eval_db.update_batch_status(batch_id, "running")
        resp = await client.post(f"/eval/api/batches/{batch_id}/cancel",
                                 json={}, headers=JSON)
        assert resp.status == 200, await resp.text()
        assert {r["status"] for r in eval_db.list_runs(batch_id)} == {"error"}
