"""Route tests for the evaluation UI's case/condition/batch writes (#109).

Each write used to assume its fields had the right type, so a ``null``, a
number or an unknown enum value reached ``.strip()``/``int()``/a SQLite
``CHECK`` and came back as a 500. These drive the real aiohttp routes
against a real ``EvalDatabase`` and expect a 400 naming the field, or a 409
for a clash with stored data.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from consensus.evaluation.eval_routes import register_eval_routes
from tests.eval_helpers import CASE_KEY, eval_db, make_run  # noqa: F401

CASE = {"case_key": "new-001", "title": "T", "presentation": "P",
        "gold_diagnosis": "G"}
PARTICIPANT = {"name": "A", "system_prompt": "s", "role": "standard",
               "provider_url": "http://p/v1", "model": "pm"}
CONDITION = {"name": "new-cond", "num_rounds": 2, "enable_da": False,
             "participants": [PARTICIPANT]}
BATCH = {"provider_url": "http://x/v1", "model": "m", "api_key": ""}


@pytest.fixture
async def client(eval_db):
    """An aiohttp test client serving only the /eval/ routes."""
    app = web.Application()
    register_eval_routes(app, eval_db)
    async with TestClient(TestServer(app)) as c:
        yield c


async def _rejected(resp, field: str, status: int = 400) -> None:
    assert resp.status == status, await resp.text()
    assert field in (await resp.json())["error"]


class TestAddCase:
    async def test_valid_case_is_created(self, client):
        resp = await client.post("/eval/api/cases",
                                 json={**CASE, "aliases": ["a"]})
        assert resp.status == 201
        assert (await resp.json())["aliases"] == ["a"]

    @pytest.mark.parametrize("field,value", [
        ("title", None), ("title", 5), ("title", "  "),
        ("source", 3), ("aliases", "a"), ("findings", [1]),
        ("differentials", None), ("difficulty", "bogus"),
    ])
    async def test_bad_field_is_400(self, client, field, value):
        resp = await client.post("/eval/api/cases",
                                 json={**CASE, field: value})
        await _rejected(resp, field)

    async def test_missing_field_is_400(self, client):
        body = {k: v for k, v in CASE.items() if k != "presentation"}
        resp = await client.post("/eval/api/cases", json=body)
        await _rejected(resp, "presentation")

    async def test_duplicate_key_is_409(self, client):
        resp = await client.post("/eval/api/cases",
                                 json={**CASE, "case_key": CASE_KEY})
        assert resp.status == 409


class TestUpdateCase:
    async def _case_id(self, eval_db) -> int:
        return eval_db.get_case_by_key(CASE_KEY)["id"]

    async def test_partial_update(self, client, eval_db):
        case_id = await self._case_id(eval_db)
        resp = await client.put(f"/eval/api/cases/{case_id}",
                                json={"title": "New", "aliases": ["x"]})
        assert resp.status == 200
        case = await resp.json()
        assert (case["title"], case["aliases"]) == ("New", ["x"])

    @pytest.mark.parametrize("field,value", [
        ("title", None), ("title", ""), ("gold_diagnosis", 1),
        ("aliases", "x"), ("difficulty", "bogus"),
    ])
    async def test_bad_field_is_400_and_nothing_changes(self, client,
                                                        eval_db, field,
                                                        value):
        case_id = await self._case_id(eval_db)
        before = eval_db.get_case(case_id)
        resp = await client.put(f"/eval/api/cases/{case_id}",
                                json={field: value})
        await _rejected(resp, field)
        assert eval_db.get_case(case_id) == before


class TestDeleteCase:
    async def test_case_with_runs_is_409(self, client, eval_db):
        make_run(eval_db)
        case_id = eval_db.get_case_by_key(CASE_KEY)["id"]
        # The UI sends this header on every call; the CSRF guard needs it.
        resp = await client.delete(
            f"/eval/api/cases/{case_id}",
            headers={"Content-Type": "application/json"})
        assert resp.status == 409
        assert eval_db.get_case(case_id) is not None


class TestConditions:
    async def test_valid_condition_keeps_participant_overrides(self,
                                                               client):
        resp = await client.post("/eval/api/conditions", json=CONDITION)
        assert resp.status == 201
        (p,) = (await resp.json())["participants"]
        assert (p["provider_url"], p["model"]) == ("http://p/v1", "pm")

    @pytest.mark.parametrize("field,value", [
        ("name", None), ("name", 7), ("description", 1),
        ("num_rounds", "abc"), ("num_rounds", 0), ("num_rounds", True),
        ("num_rounds", 2.5), ("enable_da", "yes"), ("enable_tools", 1),
        ("participants", "A"), ("participants", [{"role": "standard"}]),
        ("participants", [{**PARTICIPANT, "role": "judge"}]),
        ("participants", [{**PARTICIPANT, "model": None}]),
        ("participants", ["A"]),
    ])
    async def test_bad_field_is_400(self, client, field, value):
        resp = await client.post("/eval/api/conditions",
                                 json={**CONDITION, field: value})
        await _rejected(resp, field)

    async def test_duplicate_name_is_409(self, client, eval_db):
        name = eval_db.list_conditions()[0]["name"]
        resp = await client.post("/eval/api/conditions",
                                 json={**CONDITION, "name": name})
        assert resp.status == 409

    async def test_update_keeps_participant_overrides(self, client,
                                                      eval_db):
        """The UI re-sends every participant on save; editing a condition
        used to drop each one's provider_url/model override."""
        cond_id = eval_db.list_conditions()[0]["id"]
        resp = await client.put(f"/eval/api/conditions/{cond_id}",
                                json={"participants": [PARTICIPANT]})
        assert resp.status == 200
        (p,) = (await resp.json())["participants"]
        assert (p["provider_url"], p["model"]) == ("http://p/v1", "pm")

    @pytest.mark.parametrize("field,value", [
        ("name", ""), ("num_rounds", "abc"), ("enable_memory", "no"),
        ("participants", [{"name": ""}]),
    ])
    async def test_update_bad_field_is_400_and_nothing_changes(
            self, client, eval_db, field, value):
        cond_id = eval_db.list_conditions()[0]["id"]
        before = eval_db.get_condition(cond_id)
        resp = await client.put(f"/eval/api/conditions/{cond_id}",
                                json={field: value})
        await _rejected(resp, field)
        assert eval_db.get_condition(cond_id) == before


class TestStartBatch:
    def _body(self, eval_db, **overrides) -> dict:
        return {**BATCH,
                "case_ids": [eval_db.get_case_by_key(CASE_KEY)["id"]],
                "condition_ids": [eval_db.list_conditions()[0]["id"]],
                **overrides}

    @pytest.mark.parametrize("field,value", [
        ("provider_url", None), ("model", 3), ("api_key", None),
        ("case_ids", "1"), ("case_ids", [True]), ("condition_ids", [1.5]),
    ])
    async def test_bad_field_is_400(self, client, eval_db, field, value):
        resp = await client.post("/eval/api/batches/run",
                                 json=self._body(eval_db, **{field: value}))
        await _rejected(resp, field)
        assert eval_db.list_batches() == []

    @pytest.mark.parametrize("field", ["case_ids", "condition_ids"])
    async def test_unknown_id_is_400_and_no_batch_is_created(
            self, client, eval_db, field):
        resp = await client.post("/eval/api/batches/run",
                                 json=self._body(eval_db, **{field: [999]}))
        await _rejected(resp, "999")
        assert eval_db.list_batches() == []
