"""Shared fixtures for the evaluation-framework tests.

A real ``EvalDatabase`` on a temp file, a run factory against a seeded case,
and a stubbed ``AIClient.complete`` for the LLM judge (one layer further out
than the scorer code under test). Import the fixtures by name into a test
module to use them.
"""

import json

import pytest

from consensus.ai_client import AIClient, AIResponse
from consensus.evaluation.eval_db import EvalDatabase

#: A seeded case (``consensus/evaluation/cases.py``) — gold is Systemic Lupus
#: Erythematosus with aliases SLE / lupus / systemic lupus.
CASE_KEY = "eval-001"
GOLD = "Systemic Lupus Erythematosus"

JUDGE_SCORES = {"accuracy": 5, "reasoning": 4, "evidence": 3,
                "differential": 2, "critical_analysis": 1, "comments": "ok"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def eval_db(tmp_path):
    """A real EvalDatabase (with seeded cases/conditions) on a temp file."""
    db = EvalDatabase(str(tmp_path / "eval.db"))
    yield db
    db.conn.close()


def make_run(db: EvalDatabase, *, status: str = "done",
              conclusion: str = "", messages: tuple = ()) -> int:
    """Create a run against the seeded CASE_KEY case and return its id."""
    case = db.get_case_by_key(CASE_KEY)
    assert case is not None
    condition = db.list_conditions()[0]
    batch_id = db.create_batch("b", "http://x/v1", "m")
    run_id = db.create_run(batch_id, case["id"], condition["id"],
                           "http://x/v1", "m")
    db.update_run(run_id, status=status, conclusion=conclusion)
    for i, (speaker, content) in enumerate(messages):
        db.add_run_message(run_id, i, speaker, "participant", content)
    return run_id


@pytest.fixture
def judge_calls(monkeypatch):
    """Stub AIClient.complete; returns the list of recorded call kwargs.

    Set ``judge_calls.reply`` (str) or ``judge_calls.error`` (Exception)
    to control the outcome.
    """
    class Recorder(list):
        reply = json.dumps(JUDGE_SCORES)
        error: Exception | None = None

    rec = Recorder()

    async def fake_complete(self, messages, model, temperature=0.7,
                            max_tokens=1024):
        rec.append({"base_url": self.base_url, "api_key": self.api_key,
                    "messages": messages, "model": model,
                    "temperature": temperature, "max_tokens": max_tokens})
        if rec.error is not None:
            raise rec.error
        return AIResponse(content=rec.reply)

    monkeypatch.setattr(AIClient, "complete", fake_complete)
    return rec
