"""REST API routes for the evaluation UI.

All routes are prefixed with /eval/ and registered on the aiohttp app
by calling register_eval_routes(webapp, eval_db).
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable

from aiohttp import web

from consensus.evaluation.constants import (
    AUTO_SCORE_FAILED_PREFIX,
    FINISHED_BATCH_STATUSES,
    SQLITE_MAX_INTEGER,
)
from consensus.evaluation.eval_db import EvalDatabase
from consensus.evaluation.runner_db import execute_batch
from consensus.evaluation.request_checks import (
    BATCH_FIELDS,
    CASE_FIELDS,
    CASE_REQUIRED,
    CONDITION_FIELDS,
    CONDITION_REQUIRED,
    body_problem,
    conflict_message,
)

logger = logging.getLogger(__name__)

# Track active batch tasks so we can cancel them
_active_batch_tasks: dict[int, asyncio.Task] = {}


def _json(data: object, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


#: Methods that change state; each must carry a JSON body type.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@web.middleware
async def _require_json_writes(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Refuse non-JSON writes to /eval/api/ (CSRF guard).

    A cross-site page can send a form or ``text/plain`` POST without a
    preflight, but not ``application/json``. The main app's CSRF middleware
    only covers ``/api/``, and desktop mode serves these routes on a bare
    app with no middleware at all.
    """
    if (request.method in _WRITE_METHODS
            and request.path.startswith("/eval/api/")
            and request.content_type != "application/json"):
        return _error("Content-Type must be application/json", 415)
    return await handler(request)


@web.middleware
async def _constraint_conflicts(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Answer a known /eval/api/ constraint failure with 409, not 500.

    A duplicate case key or condition name, or deleting a case or
    condition that runs still reference, is the caller's conflict with
    stored data rather than a server fault. Any other constraint failure
    is re-raised (a 500), and so is everything outside /eval/api/: in web
    mode this middleware sits on the main app.
    """
    if not request.path.startswith("/eval/api/"):
        return await handler(request)
    try:
        return await handler(request)
    except sqlite3.IntegrityError as e:
        message = conflict_message(str(e))
        if message is None:
            raise
        logger.warning("%s %s conflicts with stored data: %s",
                       request.method, request.path, e)
        return _error(message, 409)


async def _read_object(request: web.Request) -> dict | None:
    """The request's JSON body if it is an object, else None.

    Raises ``HTTPBadRequest`` for a body holding a lone surrogate: JSON can
    spell one (``"\\ud800"``) but SQLite cannot store it.
    """
    try:
        data = await request.json()
        json.dumps(data, ensure_ascii=False).encode()
    except UnicodeEncodeError:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "Request body must be valid Unicode"}),
            content_type="application/json") from None
    # LookupError: an unknown charset; RecursionError: a too-deep body.
    except (ValueError, LookupError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _stripped(data: dict, fields: tuple[str, ...]) -> dict:
    """The given ``fields`` present in ``data``; text trimmed as on add."""
    return {f: data[f].strip() if isinstance(data[f], str) else data[f]
            for f in fields if f in data}


def _path_id(request: web.Request) -> int:
    """The URL's ``{id}``, or 0 (never a row id) if SQLite cannot store it.

    The route only promises digits; a bigger number would reach SQLite as
    an ``OverflowError`` (a 500), so 0 lets the handler's lookup answer 404.
    """
    raw = request.match_info["id"].lstrip("0")
    # Length first: int() refuses strings over 4300 digits with ValueError.
    if len(raw) > len(str(SQLITE_MAX_INTEGER)):
        return 0
    value = int(raw or "0")
    return value if value <= SQLITE_MAX_INTEGER else 0


def register_eval_routes(webapp: web.Application,
                         eval_db: EvalDatabase) -> None:
    """Register all /eval/ routes and their middlewares on the app.

    The middlewares are the CSRF guard (415) and the 409 for known
    constraint conflicts; both act only on ``/eval/api/`` paths.
    """
    webapp.middlewares.append(_require_json_writes)
    webapp.middlewares.append(_constraint_conflicts)

    eval_static_dir = os.path.realpath(
        os.path.join(os.path.dirname(__file__), "static")
    )

    # ------------------------------------------------------------------
    # Cases
    # ------------------------------------------------------------------

    async def list_cases(request: web.Request) -> web.Response:
        return _json(eval_db.list_cases())

    async def add_case(request: web.Request) -> web.Response:
        data = await _read_object(request)
        if data is None:
            return _error("Request body must be a JSON object")
        problem = body_problem(data, CASE_FIELDS, CASE_REQUIRED)
        if problem:
            return _error(problem)
        case_id = eval_db.add_case(
            case_key=data["case_key"].strip(),
            title=data["title"].strip(),
            presentation=data["presentation"].strip(),
            gold_diagnosis=data["gold_diagnosis"].strip(),
            difficulty=data.get("difficulty", "moderate"),
            source=data.get("source", ""),
            aliases=data.get("aliases", []),
            findings=data.get("findings", []),
            differentials=data.get("differentials", []),
        )
        return _json(eval_db.get_case(case_id), status=201)

    async def update_case(request: web.Request) -> web.Response:
        case_id = _path_id(request)
        # Body first: no await may fall between the lookup and the write,
        # or a concurrent delete turns the write into a foreign key 409.
        data = await _read_object(request)
        if not eval_db.get_case(case_id):
            return _error("Case not found", 404)
        if data is None:
            return _error("Request body must be a JSON object")
        problem = body_problem(data, CASE_FIELDS, CASE_REQUIRED,
                               partial=True)
        if problem:
            return _error(problem)
        kwargs = _stripped(data, ("case_key", "title", "presentation",
                                  "gold_diagnosis", "difficulty", "source"))
        eval_db.update_case(
            case_id,
            aliases=data.get("aliases"),
            findings=data.get("findings"),
            differentials=data.get("differentials"),
            **kwargs,
        )
        return _json(eval_db.get_case(case_id))

    async def delete_case(request: web.Request) -> web.Response:
        case_id = _path_id(request)
        if not eval_db.get_case(case_id):
            return _error("Case not found", 404)
        eval_db.delete_case(case_id)
        return _json({"deleted": True})

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------

    async def list_conditions(request: web.Request) -> web.Response:
        return _json(eval_db.list_conditions())

    async def add_condition(request: web.Request) -> web.Response:
        data = await _read_object(request)
        if data is None:
            return _error("Request body must be a JSON object")
        problem = body_problem(data, CONDITION_FIELDS, CONDITION_REQUIRED)
        if problem:
            return _error(problem)
        cond_id = eval_db.add_condition(
            name=data["name"].strip(),
            description=data.get("description", ""),
            enable_da=data.get("enable_da", False),
            enable_memory=data.get("enable_memory", False),
            enable_tools=data.get("enable_tools", False),
            num_rounds=data.get("num_rounds", 2),
            participants=data.get("participants", []),
        )
        return _json(eval_db.get_condition(cond_id), status=201)

    async def update_condition(request: web.Request) -> web.Response:
        cond_id = _path_id(request)
        # Body first: no await may fall between the lookup and the write,
        # or a concurrent delete turns the write into a foreign key 409.
        data = await _read_object(request)
        if not eval_db.get_condition(cond_id):
            return _error("Condition not found", 404)
        if data is None:
            return _error("Request body must be a JSON object")
        problem = body_problem(data, CONDITION_FIELDS, CONDITION_REQUIRED,
                               partial=True)
        if problem:
            return _error(problem)
        kwargs = _stripped(data, ("name", "description", "enable_da",
                                  "enable_memory", "enable_tools",
                                  "num_rounds"))
        eval_db.update_condition(
            cond_id,
            participants=data.get("participants"),
            **kwargs,
        )
        return _json(eval_db.get_condition(cond_id))

    async def delete_condition(request: web.Request) -> web.Response:
        cond_id = _path_id(request)
        if not eval_db.get_condition(cond_id):
            return _error("Condition not found", 404)
        eval_db.delete_condition(cond_id)
        return _json({"deleted": True})

    # ------------------------------------------------------------------
    # Batches
    # ------------------------------------------------------------------

    async def list_batches(request: web.Request) -> web.Response:
        return _json(eval_db.list_batches())

    async def get_batch(request: web.Request) -> web.Response:
        batch_id = _path_id(request)
        batch = eval_db.get_batch(batch_id)
        if not batch:
            return _error("Batch not found", 404)
        # Include runs and their scores for this batch
        batch["runs"] = eval_db.list_runs(batch_id)
        batch["scores"] = eval_db.get_scores_by_batch(batch_id)
        return _json(batch)

    async def start_batch(request: web.Request) -> web.Response:
        data = await _read_object(request)
        if data is None:
            return _error("Request body must be a JSON object")
        problem = body_problem(data, BATCH_FIELDS)
        if problem:
            return _error(problem)
        case_ids = data.get("case_ids", [])
        condition_ids = data.get("condition_ids", [])
        provider_url = data.get("provider_url", "").strip()
        model = data.get("model", "").strip()
        api_key = data.get("api_key", "")

        if not case_ids:
            return _error("No cases selected")
        if not condition_ids:
            return _error("No conditions selected")
        if not provider_url:
            return _error("Provider URL is required")
        if not model:
            return _error("Model name is required")
        # Checked up front: a bad id met mid-loop left a half-created batch.
        unknown = ([f"case {i}" for i in case_ids if not eval_db.get_case(i)]
                   + [f"condition {i}" for i in condition_ids
                      if not eval_db.get_condition(i)])
        if unknown:
            return _error("Unknown " + ", ".join(unknown))

        # Create batch
        batch_name = f"eval-{time.strftime('%Y%m%d-%H%M%S')}"
        batch_id = eval_db.create_batch(batch_name, provider_url, model)

        # Create run rows
        for case_id in case_ids:
            for condition_id in condition_ids:
                eval_db.create_run(
                    batch_id, case_id, condition_id, provider_url, model,
                )

        # Spawn async execution task
        task = asyncio.create_task(
            execute_batch(eval_db, batch_id, api_key)
        )
        _active_batch_tasks[batch_id] = task
        task.add_done_callback(
            lambda _t: _active_batch_tasks.pop(batch_id, None))

        return _json(eval_db.get_batch(batch_id), status=201)

    async def cancel_batch(request: web.Request) -> web.Response:
        batch_id = _path_id(request)
        batch = eval_db.get_batch(batch_id)
        if not batch:
            return _error("Batch not found", 404)
        if batch["status"] in FINISHED_BATCH_STATUSES:
            return _error(f"Batch already finished ({batch['status']})", 409)

        task = _active_batch_tasks.get(batch_id)
        if task and not task.done():
            task.cancel()  # the task stamps its interrupted run itself

        eval_db.update_batch_status(batch_id, "cancelled", time.time())
        eval_db.close_pending_runs(batch_id, "Cancelled before it started")
        return _json({"cancelled": True})

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def get_run(request: web.Request) -> web.Response:
        run_id = _path_id(request)
        run = eval_db.get_run(run_id)
        if not run:
            return _error("Run not found", 404)
        run["messages"] = eval_db.get_run_messages(run_id)
        run["scores"] = eval_db.get_scores(run_id)
        return _json(run)

    async def score_run(request: web.Request) -> web.Response:
        run_id = _path_id(request)
        run = eval_db.get_run(run_id)
        if not run:
            return _error("Run not found", 404)
        if run["status"] != "done":
            return _error("Run is not complete")

        from consensus.evaluation.scorer import score_run_from_db
        score_run_from_db(eval_db, run_id)
        if (run["error_text"] or "").startswith(AUTO_SCORE_FAILED_PREFIX):
            eval_db.update_run(run_id, error_text="")
        return _json(eval_db.get_scores(run_id))

    async def judge_run(request: web.Request) -> web.Response:
        """LLM-judge one completed run; body: provider_url, model, api_key.

        The API key is BYOK — used for this call only, never stored. A
        failed judgement returns 502 with the reason and leaves any earlier
        ``llm_judge`` score in place.
        """
        run_id = _path_id(request)
        run = eval_db.get_run(run_id)
        if not run:
            return _error("Run not found", 404)
        if run["status"] != "done":
            return _error("Run is not complete")
        data = await _read_object(request)
        if data is None:
            return _error("Request body must be a JSON object")
        provider_url = data.get("provider_url", "")
        model = data.get("model", "")
        api_key = data.get("api_key", "")
        if not all(isinstance(v, str) for v in (provider_url, model, api_key)):
            return _error("provider_url, model and api_key must be strings")
        provider_url, model = provider_url.strip(), model.strip()
        if not provider_url:
            return _error("Judge provider URL is required")
        if not model:
            return _error("Judge model is required")

        from consensus.evaluation.scorer import llm_judge_run_from_db
        result = await llm_judge_run_from_db(
            eval_db, run_id, provider_url, model, api_key,
        )
        if result is None:
            return _error("Case not found", 404)
        if result["error"]:
            return _error(f"LLM judge failed: {result['error']}", 502)
        return _json(eval_db.get_scores(run_id))

    # ------------------------------------------------------------------
    # Static files
    # ------------------------------------------------------------------

    async def serve_eval_index(request: web.Request) -> web.Response:
        return web.FileResponse(os.path.join(eval_static_dir, "eval.html"))

    async def serve_eval_static(request: web.Request) -> web.Response:
        path = request.match_info.get("path", "")
        if not path:
            return web.FileResponse(os.path.join(eval_static_dir, "eval.html"))
        filepath = os.path.realpath(os.path.join(eval_static_dir, path))
        if not filepath.startswith(eval_static_dir + os.sep):
            return web.Response(status=403, text="Forbidden")
        if os.path.isfile(filepath):
            return web.FileResponse(filepath)
        return web.Response(status=404, text="Not found")

    # ------------------------------------------------------------------
    # Register all routes
    # ------------------------------------------------------------------

    webapp.router.add_get("/eval/api/cases", list_cases)
    webapp.router.add_post("/eval/api/cases", add_case)
    webapp.router.add_put(r"/eval/api/cases/{id:\d+}", update_case)
    webapp.router.add_delete(r"/eval/api/cases/{id:\d+}", delete_case)

    webapp.router.add_get("/eval/api/conditions", list_conditions)
    webapp.router.add_post("/eval/api/conditions", add_condition)
    webapp.router.add_put(r"/eval/api/conditions/{id:\d+}", update_condition)
    webapp.router.add_delete(r"/eval/api/conditions/{id:\d+}", delete_condition)

    webapp.router.add_get("/eval/api/batches", list_batches)
    webapp.router.add_get(r"/eval/api/batches/{id:\d+}", get_batch)
    webapp.router.add_post("/eval/api/batches/run", start_batch)
    webapp.router.add_post(r"/eval/api/batches/{id:\d+}/cancel", cancel_batch)

    webapp.router.add_get(r"/eval/api/runs/{id:\d+}", get_run)
    webapp.router.add_post(r"/eval/api/runs/{id:\d+}/score", score_run)
    webapp.router.add_post(r"/eval/api/runs/{id:\d+}/judge", judge_run)

    async def redirect_eval(request: web.Request) -> web.Response:
        raise web.HTTPFound("/eval/")

    webapp.router.add_get("/eval", redirect_eval)
    webapp.router.add_get("/eval/", serve_eval_index)
    webapp.router.add_get("/eval/{path:.*}", serve_eval_static)

