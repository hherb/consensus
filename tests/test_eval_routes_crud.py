"""Route tests for the evaluation UI's case/condition/batch writes (#109).

Each write used to assume its fields had the right type, so a ``null``, a
number or an unknown enum value reached ``.strip()``/``int()``/a SQLite
``CHECK`` and came back as a 500. These drive the real aiohttp routes
against a real ``EvalDatabase`` and expect a 4xx instead: 400 naming the
bad field or id, 404 for an id no row can have, 409 for a clash with
stored data. Each also checks a refused write changed nothing.
"""

import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from consensus.evaluation.constants import (
    MAX_BATCH_RUNS, MAX_NUM_ROUNDS, SQLITE_MAX_INTEGER,
)
from consensus.evaluation import eval_routes
from consensus.evaluation.eval_routes import register_eval_routes
from tests.eval_helpers import CASE_KEY, eval_db, make_run  # noqa: F401

CASE = {"case_key": "new-001", "title": "T", "presentation": "P",
        "gold_diagnosis": "G"}
PARTICIPANT = {"name": "A", "system_prompt": "s", "role": "standard",
               "provider_url": "http://p/v1", "model": "pm"}
CONDITION = {"name": "new-cond", "num_rounds": 2, "enable_da": False,
             "participants": [PARTICIPANT]}
BATCH = {"provider_url": "http://x/v1", "model": "m", "api_key": ""}
JSON = {"Content-Type": "application/json"}
TOO_BIG = SQLITE_MAX_INTEGER + 1


@pytest.fixture
async def client(eval_db):
    """An aiohttp test client serving only the /eval/ routes."""
    app = web.Application()
    register_eval_routes(app, eval_db)
    async with TestClient(TestServer(app)) as c:
        yield c


async def _rejected(resp, field: str, inner: str = "") -> None:
    """Assert a 400 whose message is about ``field`` (and ``inner``)."""
    assert resp.status == 400, await resp.text()
    error = (await resp.json())["error"]
    assert error.startswith((field, f"Missing required field: {field}")), \
        error
    assert inner in error, error


async def _conflict(resp, wording: str) -> None:
    assert resp.status == 409, await resp.text()
    assert wording in (await resp.json())["error"]


def _released(db) -> None:
    """A refused write left no open transaction and no held lock."""
    assert not db.conn.in_transaction
    assert not db._lock.locked()


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
        ("difficulty", []), ("difficulty", {}),
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
        await _conflict(resp, "case with that case_key already exists")


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

    async def test_duplicate_key_is_409_and_nothing_changes(self, client,
                                                            eval_db):
        other = eval_db.add_case("other", "T", "P", "G", aliases=["a"])
        before = eval_db.get_case(other)
        resp = await client.put(f"/eval/api/cases/{other}",
                                json={"case_key": CASE_KEY,
                                      "aliases": ["z"]})
        await _conflict(resp, "case with that case_key already exists")
        assert eval_db.get_case(other) == before
        _released(eval_db)

    async def test_padded_duplicate_key_is_409(self, client, eval_db):
        """Updates trim text like adds, so padding cannot dodge UNIQUE."""
        other = eval_db.add_case("other", "T", "P", "G")
        resp = await client.put(f"/eval/api/cases/{other}",
                                json={"case_key": f" {CASE_KEY} "})
        await _conflict(resp, "case with that case_key already exists")
        assert eval_db.get_case(other)["case_key"] == "other"

    async def test_delete_while_reading_body_is_404(self, client, eval_db,
                                                    monkeypatch):
        """The body is read before the lookup: a delete that lands while
        it arrives is a 404, not a foreign-key 409 on the child rows."""
        other = eval_db.add_case("other", "T", "P", "G")
        real = eval_routes._read_object

        async def read_then_delete(request):
            data = await real(request)
            eval_db.delete_case(other)
            return data

        monkeypatch.setattr(eval_routes, "_read_object", read_then_delete)
        resp = await client.put(f"/eval/api/cases/{other}",
                                json={"aliases": ["x"]})
        assert resp.status == 404, await resp.text()


class TestDeleteInUse:
    """Runs keep their case and condition: deleting either is refused."""

    async def test_case_with_runs_is_409(self, client, eval_db):
        run_id = make_run(eval_db)
        case_id = eval_db.get_run(run_id)["case_id"]
        # The UI sends this header on every call; the CSRF guard needs it.
        resp = await client.delete(f"/eval/api/cases/{case_id}",
                                   headers=JSON)
        await _conflict(resp, "used by evaluation runs")
        assert eval_db.get_case(case_id) is not None

    async def test_condition_with_runs_is_409(self, client, eval_db):
        run_id = make_run(eval_db)
        cond_id = eval_db.get_run(run_id)["condition_id"]
        resp = await client.delete(f"/eval/api/conditions/{cond_id}",
                                   headers=JSON)
        await _conflict(resp, "used by evaluation runs")
        assert eval_db.get_condition(cond_id) is not None
        assert eval_db.get_run(run_id) is not None


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
        ("num_rounds", 2.5), ("num_rounds", TOO_BIG),
        ("num_rounds", MAX_NUM_ROUNDS + 1),
        ("enable_da", "yes"), ("enable_tools", 1),
    ])
    async def test_bad_field_is_400(self, client, field, value):
        resp = await client.post("/eval/api/conditions",
                                 json={**CONDITION, field: value})
        await _rejected(resp, field)

    @pytest.mark.parametrize("participants,inner", [
        ("A", "list"), (["A"], "entry 1"),
        ([{"role": "standard"}], "name"),
        ([{**PARTICIPANT, "role": "judge"}], "role"),
        ([{**PARTICIPANT, "role": []}], "role"),
        ([{**PARTICIPANT, "model": None}], "model"),
    ])
    async def test_bad_participant_is_400(self, client, participants,
                                          inner):
        resp = await client.post("/eval/api/conditions",
                                 json={**CONDITION,
                                       "participants": participants})
        await _rejected(resp, "participants", inner)

    async def test_duplicate_name_is_409(self, client, eval_db):
        name = eval_db.list_conditions()[0]["name"]
        resp = await client.post("/eval/api/conditions",
                                 json={**CONDITION, "name": name})
        await _conflict(resp, "condition with that name already exists")

    async def test_update_duplicate_name_is_409_and_nothing_changes(
            self, client, eval_db):
        first, second = eval_db.list_conditions()[:2]
        before = eval_db.get_condition(second["id"])
        resp = await client.put(f"/eval/api/conditions/{second['id']}",
                                json={"name": first["name"],
                                      "participants": [PARTICIPANT]})
        await _conflict(resp, "condition with that name already exists")
        assert eval_db.get_condition(second["id"]) == before
        _released(eval_db)

    async def test_update_padded_duplicate_name_is_409(self, client,
                                                       eval_db):
        first, second = eval_db.list_conditions()[:2]
        resp = await client.put(f"/eval/api/conditions/{second['id']}",
                                json={"name": f" {first['name']} "})
        await _conflict(resp, "condition with that name already exists")
        assert eval_db.get_condition(second["id"])["name"] == second["name"]

    async def test_delete_while_reading_body_is_404(self, client, eval_db,
                                                    monkeypatch):
        cond_id = eval_db.add_condition("doomed")
        real = eval_routes._read_object

        async def read_then_delete(request):
            data = await real(request)
            eval_db.delete_condition(cond_id)
            return data

        monkeypatch.setattr(eval_routes, "_read_object", read_then_delete)
        resp = await client.put(f"/eval/api/conditions/{cond_id}",
                                json={"participants": [PARTICIPANT]})
        assert resp.status == 404, await resp.text()

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
        ("num_rounds", MAX_NUM_ROUNDS + 1),
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
        ("case_ids", [TOO_BIG]),
    ])
    async def test_bad_field_is_400(self, client, eval_db, field, value):
        resp = await client.post("/eval/api/batches/run",
                                 json=self._body(eval_db, **{field: value}))
        await _rejected(resp, field)
        assert eval_db.list_batches() == []

    async def test_oversized_batch_is_400(self, client, eval_db):
        """#114: every run is created in one synchronous loop."""
        cond_ids = [c["id"] for c in eval_db.list_conditions()]
        case_id = eval_db.get_case_by_key(CASE_KEY)["id"]
        too_many = MAX_BATCH_RUNS // len(cond_ids) + 1
        resp = await client.post("/eval/api/batches/run", json=self._body(
            eval_db, case_ids=[case_id] * too_many, condition_ids=cond_ids))
        await _rejected(resp, "Too many runs", str(MAX_BATCH_RUNS))
        assert eval_db.list_batches() == []

    @pytest.mark.parametrize("field", ["case_ids", "condition_ids"])
    async def test_unknown_id_is_400_and_no_batch_is_created(
            self, client, eval_db, field):
        resp = await client.post("/eval/api/batches/run",
                                 json=self._body(eval_db, **{field: [999]}))
        await _rejected(resp, "Unknown", "999")
        assert eval_db.list_batches() == []

    async def test_valid_batch_creates_every_run(self, client, eval_db,
                                                 monkeypatch):
        async def no_execute(*args):
            return None
        monkeypatch.setattr(
            "consensus.evaluation.eval_routes.execute_batch", no_execute)
        case_ids = [c["id"] for c in eval_db.list_cases()]
        cond_ids = [c["id"] for c in eval_db.list_conditions()][:2]
        # More cases than conditions, so a case id with no matching
        # condition is included: the pre-check must look in the right table.
        assert max(case_ids) > max(cond_ids)
        resp = await client.post("/eval/api/batches/run",
                                 json=self._body(eval_db, case_ids=case_ids,
                                                 condition_ids=cond_ids))
        assert resp.status == 201, await resp.text()
        assert (await resp.json())["total_runs"] == (
            len(case_ids) * len(cond_ids))


class TestConflictMiddlewareScope:
    """Only /eval/api/ constraint failures are reported as conflicts."""

    async def _client_raising(self, eval_db, path: str, exc: Exception):
        app = web.Application()
        register_eval_routes(app, eval_db)

        async def boom(request):
            raise exc
        app.router.add_post(path, boom)
        return TestClient(TestServer(app))

    async def test_other_routes_keep_their_500(self, eval_db):
        async with await self._client_raising(
                eval_db, "/api/thing",
                sqlite3.IntegrityError("FOREIGN KEY constraint failed")) as c:
            resp = await c.post("/api/thing", json={})
            assert resp.status == 500

    async def test_other_sqlite_errors_keep_their_500(self, eval_db):
        async with await self._client_raising(
                eval_db, "/eval/api/thing",
                sqlite3.OperationalError("disk I/O error")) as c:
            resp = await c.post("/eval/api/thing", json={})
            assert resp.status == 500

    async def test_unknown_integrity_errors_keep_their_500(self, eval_db):
        # A CHECK/NOT NULL failure is a server bug, not the caller's conflict.
        async with await self._client_raising(
                eval_db, "/eval/api/thing",
                sqlite3.IntegrityError(
                    "NOT NULL constraint failed: cases.title")) as c:
            resp = await c.post("/eval/api/thing", json={})
            assert resp.status == 500


class TestUnstorableInput:
    """Input SQLite or the JSON decoder cannot take is a 4xx, not a 500."""

    @pytest.mark.parametrize("method,path", [
        ("PUT", "/eval/api/cases/{}"),
        ("DELETE", "/eval/api/cases/{}"),
        ("PUT", "/eval/api/conditions/{}"),
        ("DELETE", "/eval/api/conditions/{}"),
        ("GET", "/eval/api/batches/{}"),
        ("POST", "/eval/api/batches/{}/cancel"),
        ("GET", "/eval/api/runs/{}"),
        ("POST", "/eval/api/runs/{}/score"),
        ("POST", "/eval/api/runs/{}/judge"),
    ])
    @pytest.mark.parametrize("raw_id", [str(TOO_BIG), "9" * 5000])
    async def test_oversized_path_id_is_404(self, client, method, path,
                                            raw_id):
        resp = await client.request(method, path.format(raw_id), json={})
        assert resp.status == 404, await resp.text()

    async def test_unknown_charset_is_400(self, client):
        resp = await client.post(
            "/eval/api/cases", data=b"{}",
            headers={"Content-Type": "application/json; charset=bogus"})
        assert resp.status == 400, await resp.text()

    async def test_deeply_nested_body_is_400(self, client):
        resp = await client.post("/eval/api/cases", data="[" * 100_000,
                                 headers=JSON)
        assert resp.status == 400, await resp.text()

    @pytest.mark.parametrize("path,body", [
        ("/eval/api/cases",
         '{"case_key": "k\\ud800", "title": "t", "presentation": "p",'
         ' "gold_diagnosis": "g"}'),
        ("/eval/api/conditions",
         '{"name": "c", "participants": [{"name": "\\udfff"}]}'),
    ])
    async def test_lone_surrogate_is_400(self, client, eval_db, path, body):
        """JSON can spell a lone surrogate; SQLite cannot store one."""
        before = (len(eval_db.list_cases()), len(eval_db.list_conditions()))
        resp = await client.post(path, data=body, headers=JSON)
        assert resp.status == 400, await resp.text()
        assert "Unicode" in (await resp.json())["error"]
        assert (len(eval_db.list_cases()),
                len(eval_db.list_conditions())) == before

    @pytest.mark.parametrize("method,path", [
        ("POST", "/eval/api/cases"), ("PUT", "/eval/api/cases/{case}"),
        ("POST", "/eval/api/conditions"),
        ("PUT", "/eval/api/conditions/{cond}"),
        ("POST", "/eval/api/batches/run"),
    ])
    @pytest.mark.parametrize("body", [[1], "x", 5])
    async def test_non_object_body_is_400(self, client, eval_db, method,
                                          path, body):
        url = path.format(case=eval_db.get_case_by_key(CASE_KEY)["id"],
                          cond=eval_db.list_conditions()[0]["id"])
        resp = await client.request(method, url, json=body)
        assert resp.status == 400, await resp.text()
        assert "JSON object" in (await resp.json())["error"]


class TestCancelBatch:
    @pytest.mark.parametrize("status", ["done", "error", "cancelled"])
    async def test_finished_batch_is_409_and_keeps_its_status(
            self, client, eval_db, status):
        batch_id = eval_db.create_batch("b", "http://x/v1", "m")
        eval_db.update_batch_status(batch_id, status, 1.0)
        resp = await client.post(f"/eval/api/batches/{batch_id}/cancel",
                                 json={})
        await _conflict(resp, "already finished")
        assert eval_db.get_batch(batch_id)["status"] == status

    async def test_pending_batch_is_cancelled(self, client, eval_db):
        batch_id = eval_db.create_batch("b", "http://x/v1", "m")
        resp = await client.post(f"/eval/api/batches/{batch_id}/cancel",
                                 json={})
        assert resp.status == 200, await resp.text()
        assert eval_db.get_batch(batch_id)["status"] == "cancelled"


def test_ui_round_limit_mirrors_the_server():
    """The condition form's max must match MAX_NUM_ROUNDS (#112)."""
    import re
    from pathlib import Path
    html = (Path(eval_routes.__file__).parent / "static" / "eval.html"
            ).read_text()
    match = re.search(r'id="cond-rounds"[^>]*max="(\d+)"', html)
    assert match and int(match.group(1)) == MAX_NUM_ROUNDS
