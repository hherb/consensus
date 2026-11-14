"""DB-backed evaluation runner, used by the web UI via ``eval_routes.py``.

Reads a run's case and condition from the eval DB, drives a temporary
``ConsensusApp`` (or a single direct chat call for the one-participant,
one-round baseline), and writes messages and the conclusion back to the
eval DB. The in-memory CLI path lives in ``runner.py``.
"""

import asyncio
import logging
import os
import tempfile
import time

from consensus.app import ConsensusApp

from consensus.evaluation.constants import AUTO_SCORE_FAILED_PREFIX
from consensus.evaluation.runner_common import (
    MODERATOR_COLOR, MODERATOR_SYSTEM_PROMPT, MODERATOR_TEMPERATURE,
    PARTICIPANT_COLOR, PARTICIPANT_TEMPERATURE, SINGLE_CHAT_MAX_TOKENS,
    TURN_MAX_TOKENS, conclude, discussion_topic, drive_turns, join_errors,
)

logger = logging.getLogger(__name__)


async def execute_batch(eval_db, batch_id: int, api_key: str = "") -> None:
    """Execute all pending runs in a batch. Runs as a background task.

    Each run is auto-scored after it finishes. A scoring failure is logged
    and noted in the run's ``error_text`` but leaves it "done" — the run
    itself succeeded and can be rescored (#114).
    """
    try:
        eval_db.update_batch_status(batch_id, "running")
        runs = eval_db.list_runs(batch_id)
        pending = [r for r in runs if r["status"] == "pending"]

        for run_data in pending:
            run_id = run_data["id"]
            try:
                await run_case_condition_db(
                    eval_db=eval_db,
                    run_id=run_id,
                    api_key=api_key,
                )
            except asyncio.CancelledError:
                eval_db.update_run(run_id, status="error",
                                   error_text="Cancelled",
                                   completed_at=time.time())
                raise
            except Exception as e:
                logger.exception("Run %d failed", run_id)
                eval_db.update_run(
                    run_id, status="error", error_text=str(e),
                    completed_at=time.time(),
                )
                continue

            try:
                from consensus.evaluation.scorer import score_run_from_db
                score_run_from_db(eval_db, run_id)
            except Exception as e:
                logger.exception("Auto-scoring run %d failed", run_id)
                eval_db.update_run(
                    run_id, error_text=f"{AUTO_SCORE_FAILED_PREFIX}{e}")

        eval_db.update_batch_status(batch_id, "done", time.time())

    except asyncio.CancelledError:
        eval_db.update_batch_status(batch_id, "cancelled", time.time())
    except Exception:
        logger.exception("Batch %d failed", batch_id)
        eval_db.update_batch_status(batch_id, "error", time.time())


async def run_case_condition_db(
    eval_db,
    run_id: int,
    api_key: str = "",
    num_rounds_override: int | None = None,
) -> None:
    """Run a single case/condition from eval DB, writing results back to DB.

    Reads case and condition data from the eval DB, creates a temporary
    ConsensusApp, runs the discussion, and writes messages/conclusion
    back to the eval DB.
    """
    run = eval_db.get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    case = eval_db.get_case(run["case_id"])
    condition = eval_db.get_condition(run["condition_id"])
    if not case or not condition:
        raise ValueError("Case or condition not found for run")

    provider_url = run["provider_url"]
    model = run["model"]
    participants = condition.get("participants", [])
    num_rounds = num_rounds_override or condition["num_rounds"]

    eval_db.update_run(run_id, status="running", started_at=time.time())

    is_single = len(participants) == 1 and num_rounds <= 1

    try:
        if is_single:
            await _run_single_chat(
                eval_db, run_id, case, participants[0],
                provider_url, model, api_key)
        else:
            await _run_multi_party(
                eval_db, run_id, case, condition, participants,
                provider_url, model, api_key, num_rounds)
    except Exception as e:
        logger.exception("DB run failed for run_id=%d", run_id)
        eval_db.update_run(
            run_id, status="error", error_text=str(e),
            completed_at=time.time())


async def _run_single_chat(
    eval_db, run_id: int, case: dict, participant: dict,
    provider_url: str, model: str, api_key: str,
) -> None:
    """Single-participant baseline: direct chat API call, no Consensus overhead."""
    from consensus.ai_client import AIClient

    p_url = participant.get("provider_url", "").strip() or provider_url
    p_model = participant.get("model", "").strip() or model
    system_prompt = participant.get("system_prompt", "")

    client = AIClient(base_url=p_url, api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": (
            f"Clinical Case:\n\n{case['presentation']}\n\n"
            f"Please analyse this case systematically. Identify the key "
            f"findings, generate a differential diagnosis, and state "
            f"the most likely diagnosis with supporting reasoning."
        ),
    })

    response = await client.complete(
        messages=messages, model=p_model,
        temperature=PARTICIPANT_TEMPERATURE,
        max_tokens=SINGLE_CHAT_MAX_TOKENS,
    )

    eval_db.add_run_message(
        run_id=run_id, turn_index=0,
        speaker=participant["name"], role="standard",
        content=response.content,
        model_used=response.model,
        tokens=response.total_tokens,
    )

    eval_db.update_run(
        run_id, status="done",
        conclusion=response.content,
        num_turns=1,
        total_tokens=response.total_tokens,
        total_latency_ms=response.latency_ms,
        completed_at=time.time(),
    )


async def _run_multi_party(
    eval_db, run_id: int, case: dict, condition: dict,
    participants: list[dict], provider_url: str, model: str,
    api_key: str, num_rounds: int,
) -> None:
    """Multi-participant discussion via full Consensus machinery.

    ``api_key`` reaches the temp app's providers as per-request BYOK keys
    (a ContextVar, so concurrent batches cannot see each other's key). It
    is set even when empty: the batch task inherits the starting request's
    BYOK keys, which are keyed by the main app's provider ids and must not
    land on the temp app's providers with the same ids.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "eval.db")
        app = ConsensusApp(db_path=db_path)

        provider = app.db.add_provider("eval-provider", provider_url, "")

        # Create moderator
        mod_id = app.db.add_entity(
            name="Moderator", entity_type="ai",
            avatar_color=MODERATOR_COLOR, provider_id=provider,
            model=model, temperature=MODERATOR_TEMPERATURE,
            max_tokens=TURN_MAX_TOKENS,
            system_prompt=MODERATOR_SYSTEM_PROMPT,
        )

        # Create participants (with optional per-participant provider/model)
        _extra_providers: dict[str, int] = {}  # provider_url -> provider_id
        entity_ids: dict[str, int] = {}
        for p in participants:
            p_provider = provider
            p_model = model
            p_url = p.get("provider_url", "").strip()
            p_mdl = p.get("model", "").strip()
            if p_url:
                if p_url not in _extra_providers:
                    _extra_providers[p_url] = app.db.add_provider(
                        f"eval-{p['name']}", p_url, "")
                p_provider = _extra_providers[p_url]
            if p_mdl:
                p_model = p_mdl
            eid = app.db.add_entity(
                name=p["name"], entity_type="ai",
                avatar_color=PARTICIPANT_COLOR, provider_id=p_provider,
                model=p_model, temperature=PARTICIPANT_TEMPERATURE,
                max_tokens=TURN_MAX_TOKENS,
                system_prompt=p.get("system_prompt", ""),
            )
            entity_ids[p["name"]] = eid
            if condition["enable_tools"]:
                app.assign_tool_to_entity(eid, "web_search", "private")
            if condition["enable_memory"]:
                for tool in ["memory_store", "memory_recall",
                             "discussion_search"]:
                    app.assign_tool_to_entity(eid, tool, "private")

        provider_ids = [provider, *_extra_providers.values()]
        ConsensusApp.set_request_api_keys(
            {str(pid): api_key for pid in provider_ids} if api_key else {})

        # Build discussion
        app.add_to_discussion(mod_id, is_moderator=True)
        for p in participants:
            app.add_to_discussion(
                entity_ids[p["name"]],
                participant_role=p.get("role", "standard"),
            )

        app.set_topic(discussion_topic(case["presentation"]))

        start_result = app.start_discussion(moderator_participates=False)
        if "error" in start_result:
            eval_db.update_run(
                run_id, status="error", error_text=start_result["error"],
                completed_at=time.time())
            return

        totals = {"tokens": 0, "latency": 0, "turn_index": 0}

        def record(speaker, turn_result: dict) -> None:
            tokens = turn_result.get("total_tokens", 0)
            totals["tokens"] += tokens
            totals["latency"] += turn_result.get("latency_ms", 0)
            eval_db.add_run_message(
                run_id=run_id,
                turn_index=totals["turn_index"],
                speaker=speaker.name,
                role=app.discussion.member_roles.get(
                    speaker.id, "standard"),
                content=turn_result.get("content", ""),
                model_used=turn_result.get("model_used", ""),
                tokens=tokens,
            )
            totals["turn_index"] += 1

        turns_completed, turn_error = await drive_turns(
            app, len(participants) * num_rounds, record)
        conclusion, conclusion_error = await conclude(app)
        error = join_errors(turn_error, conclusion_error)

        # A truncated or unsynthesised run is not a result: "error" keeps
        # it out of auto-scoring while its partial transcript stays visible.
        eval_db.update_run(
            run_id, status="error" if error else "done",
            error_text=error,
            conclusion=conclusion,
            num_turns=turns_completed,
            total_tokens=totals["tokens"],
            total_latency_ms=totals["latency"],
            completed_at=time.time(),
        )
