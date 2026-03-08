"""REST API routes for the evaluation UI.

All routes are prefixed with /eval/ and registered on the aiohttp app
by calling register_eval_routes(webapp, eval_db).
"""

import asyncio
import json
import logging
import os
import time

from aiohttp import web

from evaluation.eval_db import EvalDatabase

logger = logging.getLogger(__name__)

# Track active batch tasks so we can cancel them
_active_batch_tasks: dict[int, asyncio.Task] = {}


def _json(data: object, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def register_eval_routes(webapp: web.Application,
                         eval_db: EvalDatabase) -> None:
    """Register all /eval/ routes on the aiohttp application."""

    eval_static_dir = os.path.realpath(
        os.path.join(os.path.dirname(__file__), "static")
    )

    # ------------------------------------------------------------------
    # Cases
    # ------------------------------------------------------------------

    async def list_cases(request: web.Request) -> web.Response:
        return _json(eval_db.list_cases())

    async def add_case(request: web.Request) -> web.Response:
        data = await request.json()
        required = ("case_key", "title", "presentation", "gold_diagnosis")
        for field in required:
            if not data.get(field, "").strip():
                return _error(f"Missing required field: {field}")
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
        case_id = int(request.match_info["id"])
        if not eval_db.get_case(case_id):
            return _error("Case not found", 404)
        data = await request.json()
        kwargs = {}
        for field in ("case_key", "title", "presentation", "gold_diagnosis",
                       "difficulty", "source"):
            if field in data:
                kwargs[field] = data[field]
        eval_db.update_case(
            case_id,
            aliases=data.get("aliases"),
            findings=data.get("findings"),
            differentials=data.get("differentials"),
            **kwargs,
        )
        return _json(eval_db.get_case(case_id))

    async def delete_case(request: web.Request) -> web.Response:
        case_id = int(request.match_info["id"])
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
        data = await request.json()
        if not data.get("name", "").strip():
            return _error("Missing required field: name")
        cond_id = eval_db.add_condition(
            name=data["name"].strip(),
            description=data.get("description", ""),
            enable_da=bool(data.get("enable_da", False)),
            enable_memory=bool(data.get("enable_memory", False)),
            enable_tools=bool(data.get("enable_tools", False)),
            num_rounds=int(data.get("num_rounds", 2)),
            participants=data.get("participants", []),
        )
        return _json(eval_db.get_condition(cond_id), status=201)

    async def update_condition(request: web.Request) -> web.Response:
        cond_id = int(request.match_info["id"])
        if not eval_db.get_condition(cond_id):
            return _error("Condition not found", 404)
        data = await request.json()
        kwargs = {}
        for field in ("name", "description", "enable_da", "enable_memory",
                       "enable_tools", "num_rounds"):
            if field in data:
                kwargs[field] = data[field]
        eval_db.update_condition(
            cond_id,
            participants=data.get("participants"),
            **kwargs,
        )
        return _json(eval_db.get_condition(cond_id))

    async def delete_condition(request: web.Request) -> web.Response:
        cond_id = int(request.match_info["id"])
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
        batch_id = int(request.match_info["id"])
        batch = eval_db.get_batch(batch_id)
        if not batch:
            return _error("Batch not found", 404)
        # Include runs for this batch
        batch["runs"] = eval_db.list_runs(batch_id)
        return _json(batch)

    async def start_batch(request: web.Request) -> web.Response:
        data = await request.json()
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
            _execute_batch(eval_db, batch_id, api_key)
        )
        _active_batch_tasks[batch_id] = task

        return _json(eval_db.get_batch(batch_id), status=201)

    async def cancel_batch(request: web.Request) -> web.Response:
        batch_id = int(request.match_info["id"])
        batch = eval_db.get_batch(batch_id)
        if not batch:
            return _error("Batch not found", 404)

        task = _active_batch_tasks.get(batch_id)
        if task and not task.done():
            task.cancel()

        eval_db.update_batch_status(batch_id, "cancelled", time.time())
        return _json({"cancelled": True})

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def get_run(request: web.Request) -> web.Response:
        run_id = int(request.match_info["id"])
        run = eval_db.get_run(run_id)
        if not run:
            return _error("Run not found", 404)
        run["messages"] = eval_db.get_run_messages(run_id)
        run["scores"] = eval_db.get_scores(run_id)
        return _json(run)

    async def score_run(request: web.Request) -> web.Response:
        run_id = int(request.match_info["id"])
        run = eval_db.get_run(run_id)
        if not run:
            return _error("Run not found", 404)
        if run["status"] != "done":
            return _error("Run is not complete")

        from evaluation.scorer import score_run_from_db
        score_run_from_db(eval_db, run_id)
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
    webapp.router.add_put("/eval/api/cases/{id}", update_case)
    webapp.router.add_delete("/eval/api/cases/{id}", delete_case)

    webapp.router.add_get("/eval/api/conditions", list_conditions)
    webapp.router.add_post("/eval/api/conditions", add_condition)
    webapp.router.add_put("/eval/api/conditions/{id}", update_condition)
    webapp.router.add_delete("/eval/api/conditions/{id}", delete_condition)

    webapp.router.add_get("/eval/api/batches", list_batches)
    webapp.router.add_get("/eval/api/batches/{id}", get_batch)
    webapp.router.add_post("/eval/api/batches/run", start_batch)
    webapp.router.add_post("/eval/api/batches/{id}/cancel", cancel_batch)

    webapp.router.add_get("/eval/api/runs/{id}", get_run)
    webapp.router.add_post("/eval/api/runs/{id}/score", score_run)

    webapp.router.add_get("/eval/", serve_eval_index)
    webapp.router.add_get("/eval/{path:.*}", serve_eval_static)


# ---------------------------------------------------------------------------
# Batch execution (runs as asyncio task)
# ---------------------------------------------------------------------------

async def _execute_batch(eval_db: EvalDatabase, batch_id: int,
                         api_key: str = "") -> None:
    """Execute all pending runs in a batch. Runs as a background task."""
    try:
        eval_db.update_batch_status(batch_id, "running")
        runs = eval_db.list_runs(batch_id)
        pending = [r for r in runs if r["status"] == "pending"]

        for run_data in pending:
            run_id = run_data["id"]
            try:
                from evaluation.runner import run_case_condition_db
                await run_case_condition_db(
                    eval_db=eval_db,
                    run_id=run_id,
                    api_key=api_key,
                )
                # Auto-score after completion
                from evaluation.scorer import score_run_from_db
                score_run_from_db(eval_db, run_id)

            except asyncio.CancelledError:
                eval_db.update_run(run_id, status="error",
                                   error_text="Cancelled")
                raise
            except Exception as e:
                logger.exception("Run %d failed", run_id)
                eval_db.update_run(
                    run_id, status="error", error_text=str(e),
                    completed_at=time.time(),
                )

        eval_db.update_batch_status(batch_id, "done", time.time())

    except asyncio.CancelledError:
        eval_db.update_batch_status(batch_id, "cancelled", time.time())
    except Exception as e:
        logger.exception("Batch %d failed", batch_id)
        eval_db.update_batch_status(batch_id, "error", time.time())
    finally:
        _active_batch_tasks.pop(batch_id, None)
