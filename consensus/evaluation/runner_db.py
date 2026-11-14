"""DB-backed evaluation runner, used by the web UI via ``eval_routes.py``.

Reads a run's case and condition from the eval DB, drives a temporary
``ConsensusApp`` (or a single direct chat call for the one-participant,
one-round baseline), and writes messages and the conclusion back to the
eval DB. The in-memory CLI path lives in ``runner.py``.
"""

import logging
import os
import tempfile
import time

from consensus.app import ConsensusApp
from consensus.models import EntityType

logger = logging.getLogger(__name__)


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
    finally:
        os.environ.pop("_EVAL_TEMP_KEY", None)


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
        temperature=0.7, max_tokens=2048,
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
    """Multi-participant discussion via full Consensus machinery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "eval.db")
        app = ConsensusApp(db_path=db_path)

        # Setup provider — use a dummy env var and set key directly
        if api_key:
            os.environ["_EVAL_TEMP_KEY"] = api_key
            provider = app.db.add_provider(
                "eval-provider", provider_url, "_EVAL_TEMP_KEY")
        else:
            provider = app.db.add_provider(
                "eval-provider", provider_url, "")

        # Create moderator
        mod_id = app.db.add_entity(
            name="Moderator", entity_type="ai",
            avatar_color="#6b7280", provider_id=provider,
            model=model, temperature=0.5, max_tokens=1024,
            system_prompt=(
                "You are a medical discussion moderator. Summarise each "
                "participant's contribution concisely. In the final "
                "synthesis, state the most likely diagnosis clearly, list "
                "the key supporting findings, and note any significant "
                "disagreements or alternative diagnoses raised."
            ),
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
                    if api_key:
                        _extra_providers[p_url] = app.db.add_provider(
                            f"eval-{p['name']}", p_url, "_EVAL_TEMP_KEY")
                    else:
                        _extra_providers[p_url] = app.db.add_provider(
                            f"eval-{p['name']}", p_url, "")
                p_provider = _extra_providers[p_url]
            if p_mdl:
                p_model = p_mdl
            eid = app.db.add_entity(
                name=p["name"], entity_type="ai",
                avatar_color="#3b82f6", provider_id=p_provider,
                model=p_model, temperature=0.7, max_tokens=1024,
                system_prompt=p.get("system_prompt", ""),
            )
            entity_ids[p["name"]] = eid
            if condition["enable_tools"]:
                app.assign_tool_to_entity(eid, "web_search", "private")
            if condition["enable_memory"]:
                for tool in ["memory_store", "memory_recall",
                             "discussion_search"]:
                    app.assign_tool_to_entity(eid, tool, "private")

        # Build discussion
        app.add_to_discussion(mod_id, is_moderator=True)
        for p in participants:
            app.add_to_discussion(
                entity_ids[p["name"]],
                participant_role=p.get("role", "standard"),
            )

        topic = (
            f"Clinical Case Discussion\n\n{case['presentation']}\n\n"
            f"Please discuss this case systematically. Consider the key "
            f"findings, generate a differential diagnosis, and work toward "
            f"the most likely diagnosis with supporting reasoning."
        )
        app.set_topic(topic)

        start_result = app.start_discussion(moderator_participates=False)
        if "error" in start_result:
            eval_db.update_run(
                run_id, status="error", error_text=start_result["error"],
                completed_at=time.time())
            return

        # Run turns
        turns_completed = 0
        total_tokens = 0
        total_latency = 0
        total_turns = len(participants) * num_rounds
        turn_index = 0

        for _ in range(total_turns):
            current = app.discussion.current_speaker
            if not current or current.entity_type != EntityType.AI:
                break

            turn_result = await app.generate_ai_turn()
            if "error" in turn_result:
                logger.warning("Turn error: %s", turn_result["error"])
                break

            tokens = turn_result.get("total_tokens", 0)
            latency = turn_result.get("latency_ms", 0)
            total_tokens += tokens
            total_latency += latency

            eval_db.add_run_message(
                run_id=run_id,
                turn_index=turn_index,
                speaker=current.name,
                role=app.discussion.member_roles.get(
                    current.id, "standard"),
                content=turn_result.get("content", ""),
                model_used=turn_result.get("model_used", ""),
                tokens=tokens,
            )
            turn_index += 1

            await app.complete_turn()
            turns_completed += 1

        # Conclude
        await app.conclude_discussion()

        # Extract conclusion
        conclusion = ""
        for msg in reversed(app.discussion.messages):
            if (msg.role.value == "moderator"
                    and "Final Synthesis" in msg.content):
                conclusion = msg.content
                break
        if not conclusion:
            for msg in reversed(app.discussion.messages):
                if msg.role.value == "moderator":
                    conclusion = msg.content
                    break

        eval_db.update_run(
            run_id, status="done",
            conclusion=conclusion,
            num_turns=turns_completed,
            total_tokens=total_tokens,
            total_latency_ms=total_latency,
            completed_at=time.time(),
        )
