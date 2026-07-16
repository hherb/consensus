"""Evaluation runner — executes case vignettes under each condition.

Usage:
    python -m consensus.evaluation.runner [OPTIONS]

Options:
    --cases CASE_IDS        Comma-separated case IDs (default: all)
    --conditions COND_NAMES Comma-separated condition names (default: all)
    --provider-url URL      OpenAI-compatible API base URL
    --model MODEL           Model name to use for all participants
    --api-key-env ENV_VAR   Environment variable holding the API key
    --output DIR            Output directory for results (default: evaluation/results)
    --num-rounds N          Override number of discussion rounds
    --dry-run               Print config without running
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from consensus.app import ConsensusApp
from consensus.models import EntityType

from consensus.evaluation.cases import CASES, CaseVignette
from consensus.evaluation.conditions import CONDITIONS, Condition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures for results
# ---------------------------------------------------------------------------

class RunResult:
    """Result of a single (case, condition) run."""

    def __init__(self, case: CaseVignette, condition: Condition):
        self.case_id = case.id
        self.case_title = case.title
        self.condition_name = condition.name
        self.gold_diagnosis = case.gold_diagnosis
        self.messages: list[dict] = []
        self.conclusion: str = ""
        self.total_tokens: int = 0
        self.total_latency_ms: int = 0
        self.num_turns: int = 0
        self.error: str = ""
        self.timestamp: float = time.time()

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "case_title": self.case_title,
            "condition": self.condition_name,
            "gold_diagnosis": self.gold_diagnosis,
            "conclusion": self.conclusion,
            "num_turns": self.num_turns,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "error": self.error,
            "timestamp": self.timestamp,
            "messages": self.messages,
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_case_condition(
    case: CaseVignette,
    condition: Condition,
    provider_url: str,
    model: str,
    api_key_env: str = "",
    num_rounds_override: int | None = None,
) -> RunResult:
    """Run a single case under a single condition, returning the result."""
    result = RunResult(case, condition)
    num_rounds = num_rounds_override or condition.num_rounds

    # Use a temporary database for isolation
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "eval.db")
        app = ConsensusApp(db_path=db_path)

        try:
            # --- Setup provider ---
            provider = app.db.add_provider(
                "eval-provider", provider_url, api_key_env,
            )

            # --- Create moderator ---
            mod_id = app.db.add_entity(
                name="Moderator",
                entity_type="ai",
                avatar_color="#6b7280",
                provider_id=provider,
                model=model,
                temperature=0.5,
                max_tokens=1024,
                system_prompt=(
                    "You are a medical discussion moderator. Summarise each "
                    "participant's contribution concisely. In the final "
                    "synthesis, state the most likely diagnosis clearly, list "
                    "the key supporting findings, and note any significant "
                    "disagreements or alternative diagnoses raised."
                ),
            )

            # --- Create participants ---
            entity_ids: dict[str, int] = {}
            for spec in condition.participants:
                eid = app.db.add_entity(
                    name=spec.name,
                    entity_type="ai",
                    avatar_color="#3b82f6",
                    provider_id=provider,
                    model=model,
                    temperature=0.7,
                    max_tokens=1024,
                    system_prompt=spec.system_prompt,
                )
                entity_ids[spec.name] = eid

                # Assign tools if condition enables them
                if condition.enable_tools:
                    app.assign_tool_to_entity(eid, "web_search", "private")
                if condition.enable_memory:
                    for tool in ["memory_store", "memory_recall", "discussion_search"]:
                        app.assign_tool_to_entity(eid, tool, "private")

            # --- Build discussion ---
            app.add_to_discussion(mod_id, is_moderator=True)
            for spec in condition.participants:
                eid = entity_ids[spec.name]
                app.add_to_discussion(
                    eid,
                    participant_role=spec.role,
                )

            # --- Set topic (the clinical vignette) ---
            topic = (
                f"Clinical Case Discussion\n\n{case.presentation}\n\n"
                f"Please discuss this case systematically. Consider the key "
                f"findings, generate a differential diagnosis, and work toward "
                f"the most likely diagnosis with supporting reasoning."
            )
            app.set_topic(topic)

            # --- Start discussion ---
            start_result = app.start_discussion(moderator_participates=False)
            if "error" in start_result:
                result.error = start_result["error"]
                return result

            # --- Run rounds ---
            turns_completed = 0
            participants_per_round = len(condition.participants)
            total_turns = participants_per_round * num_rounds

            for turn_idx in range(total_turns):
                current = app.discussion.current_speaker
                if not current:
                    break
                if current.entity_type != EntityType.AI:
                    break

                logger.info(
                    "  Turn %d/%d: %s speaking...",
                    turn_idx + 1, total_turns, current.name,
                )

                # Generate AI turn
                turn_result = await app.generate_ai_turn()
                if "error" in turn_result:
                    logger.warning("  Turn error: %s", turn_result["error"])
                    result.error = turn_result["error"]
                    break

                # Track tokens
                result.total_tokens += turn_result.get("total_tokens", 0)
                result.total_latency_ms += turn_result.get("latency_ms", 0)

                # Record message
                result.messages.append({
                    "speaker": current.name,
                    "role": app.discussion.member_roles.get(current.id, "standard"),
                    "content": turn_result.get("content", ""),
                    "model": turn_result.get("model_used", ""),
                    "tokens": turn_result.get("total_tokens", 0),
                })

                # Complete turn (moderator summarises, advance)
                complete_result = await app.complete_turn()
                if "error" in complete_result:
                    logger.warning("  Complete turn error: %s", complete_result["error"])
                    break

                turns_completed += 1

            result.num_turns = turns_completed

            # --- Conclude ---
            conclude_result = await app.conclude_discussion()

            # Extract conclusion from the last moderator message
            for msg in reversed(app.discussion.messages):
                if msg.role.value == "moderator" and "Final Synthesis" in msg.content:
                    result.conclusion = msg.content
                    break

            # If no formal conclusion, use the last moderator message
            if not result.conclusion:
                for msg in reversed(app.discussion.messages):
                    if msg.role.value == "moderator":
                        result.conclusion = msg.content
                        break

        except Exception as e:
            logger.exception("Run failed for case=%s condition=%s", case.id, condition.name)
            result.error = str(e)

    return result


async def run_evaluation(
    cases: list[CaseVignette],
    conditions: list[Condition],
    provider_url: str,
    model: str,
    api_key_env: str = "",
    output_dir: str = "",
    num_rounds_override: int | None = None,
) -> list[RunResult]:
    """Run all (case, condition) combinations and save results."""
    output_path = Path(output_dir) if output_dir else Path(__file__).parent / "results"
    output_path.mkdir(parents=True, exist_ok=True)

    results: list[RunResult] = []
    total = len(cases) * len(conditions)
    completed = 0

    for case in cases:
        for condition in conditions:
            completed += 1
            logger.info(
                "[%d/%d] Case: %s | Condition: %s",
                completed, total, case.id, condition.name,
            )

            result = await run_case_condition(
                case, condition, provider_url, model, api_key_env,
                num_rounds_override=num_rounds_override,
            )
            results.append(result)

            # Save incrementally (one file per run)
            run_file = output_path / f"{case.id}_{condition.name}.json"
            with open(run_file, "w") as f:
                json.dump(result.to_dict(), f, indent=2)

            logger.info(
                "  Done: %d turns, %d tokens, error=%s",
                result.num_turns, result.total_tokens,
                result.error or "none",
            )

    # Save combined results
    combined_file = output_path / "all_results.json"
    with open(combined_file, "w") as f:
        json.dump([r.to_dict() for r in results], f, indent=2)

    logger.info("Results saved to %s", output_path)
    return results


# ---------------------------------------------------------------------------
# DB-aware runner (used by the web UI via eval_routes.py)
# ---------------------------------------------------------------------------

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
    from consensus.evaluation.eval_db import EvalDatabase

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Consensus evaluation on medical case vignettes",
    )
    parser.add_argument(
        "--cases", type=str, default="",
        help="Comma-separated case IDs (default: all)",
    )
    parser.add_argument(
        "--conditions", type=str, default="",
        help="Comma-separated condition names (default: all)",
    )
    parser.add_argument(
        "--provider-url", type=str,
        default=os.environ.get("EVAL_PROVIDER_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible API base URL",
    )
    parser.add_argument(
        "--model", type=str,
        default=os.environ.get("EVAL_MODEL", "llama3"),
        help="Model name for all participants",
    )
    parser.add_argument(
        "--api-key-env", type=str,
        default=os.environ.get("EVAL_API_KEY_ENV", ""),
        help="Env var name holding the API key",
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="Output directory for results",
    )
    parser.add_argument(
        "--num-rounds", type=int, default=None,
        help="Override number of discussion rounds",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print configuration without running",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Select cases
    if args.cases:
        case_ids = [c.strip() for c in args.cases.split(",")]
        cases = [c for c in CASES if c.id in case_ids]
        if not cases:
            logger.error("No cases found for IDs: %s", case_ids)
            sys.exit(1)
    else:
        cases = CASES

    # Select conditions
    if args.conditions:
        cond_names = [c.strip() for c in args.conditions.split(",")]
        conditions = [CONDITIONS[n] for n in cond_names if n in CONDITIONS]
        if not conditions:
            logger.error("No conditions found for names: %s", cond_names)
            sys.exit(1)
    else:
        conditions = list(CONDITIONS.values())

    total_runs = len(cases) * len(conditions)

    if args.dry_run:
        print(f"\nEvaluation configuration:")
        print(f"  Provider: {args.provider_url}")
        print(f"  Model:    {args.model}")
        print(f"  API key:  {'(set)' if os.environ.get(args.api_key_env) else '(not set)' if args.api_key_env else '(none needed)'}")
        print(f"  Cases:    {len(cases)}")
        for c in cases:
            print(f"    - {c.id}: {c.title} [{c.difficulty}]")
        print(f"  Conditions: {len(conditions)}")
        for cond in conditions:
            print(f"    - {cond.name}: {cond.description}")
        print(f"  Total runs: {total_runs}")
        if args.num_rounds:
            print(f"  Rounds override: {args.num_rounds}")
        print()
        return

    logger.info(
        "Starting evaluation: %d cases x %d conditions = %d runs",
        len(cases), len(conditions), total_runs,
    )

    results = asyncio.run(run_evaluation(
        cases=cases,
        conditions=conditions,
        provider_url=args.provider_url,
        model=args.model,
        api_key_env=args.api_key_env,
        output_dir=args.output,
        num_rounds_override=args.num_rounds,
    ))

    # Quick summary
    print(f"\n{'='*70}")
    print(f"EVALUATION COMPLETE: {len(results)} runs")
    print(f"{'='*70}")
    errors = [r for r in results if r.error]
    if errors:
        print(f"  Errors: {len(errors)}")
        for r in errors:
            print(f"    - {r.case_id}/{r.condition_name}: {r.error[:80]}")
    print(f"  Successful: {len(results) - len(errors)}")
    total_tokens = sum(r.total_tokens for r in results)
    print(f"  Total tokens: {total_tokens:,}")
    print(f"\nRun scoring with: python -m consensus.evaluation.scorer --input <results_dir>")


if __name__ == "__main__":
    main()
