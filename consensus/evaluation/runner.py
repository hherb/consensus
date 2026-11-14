"""Evaluation runner — executes case vignettes under each condition.

Usage:
    python -m consensus.evaluation.runner [OPTIONS]

Options:
    --cases CASE_IDS        Comma-separated case IDs (default: all)
    --conditions COND_NAMES Comma-separated condition names (default: all)
    --provider-url URL      OpenAI-compatible API base URL
    --model MODEL           Model name to use for all participants
    --api-key-env ENV_VAR   Environment variable holding the API key
    --output DIR            Output directory for results
                            (default: <app data dir>/eval_results)
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
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from consensus.app import ConsensusApp
from consensus.config import get_data_dir

from consensus.evaluation.cases import CASES, CaseVignette
from consensus.evaluation.conditions import CONDITIONS, Condition
from consensus.evaluation.constants import DEFAULT_MODEL, DEFAULT_PROVIDER_URL
from consensus.evaluation.runner_common import (
    MODERATOR_COLOR, MODERATOR_SYSTEM_PROMPT, MODERATOR_TEMPERATURE,
    PARTICIPANT_COLOR, PARTICIPANT_TEMPERATURE, TURN_MAX_TOKENS, conclude,
    discussion_topic, drive_turns, join_errors,
)

logger = logging.getLogger(__name__)

#: Results directory under the app data dir when ``--output`` is not given
#: (beside the package it would land in site-packages for a wheel install).
RESULTS_DIRNAME = "eval_results"


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
                avatar_color=MODERATOR_COLOR,
                provider_id=provider,
                model=model,
                temperature=MODERATOR_TEMPERATURE,
                max_tokens=TURN_MAX_TOKENS,
                system_prompt=MODERATOR_SYSTEM_PROMPT,
            )

            # --- Create participants ---
            entity_ids: dict[str, int] = {}
            for spec in condition.participants:
                eid = app.db.add_entity(
                    name=spec.name,
                    entity_type="ai",
                    avatar_color=PARTICIPANT_COLOR,
                    provider_id=provider,
                    model=model,
                    temperature=PARTICIPANT_TEMPERATURE,
                    max_tokens=TURN_MAX_TOKENS,
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
            app.set_topic(discussion_topic(case.presentation))

            # --- Start discussion ---
            start_result = app.start_discussion(moderator_participates=False)
            if "error" in start_result:
                result.error = start_result["error"]
                return result

            # --- Run rounds ---
            def record(speaker, turn_result: dict) -> None:
                result.total_tokens += turn_result.get("total_tokens", 0)
                result.total_latency_ms += turn_result.get("latency_ms", 0)
                result.messages.append({
                    "speaker": speaker.name,
                    "role": app.discussion.member_roles.get(speaker.id, "standard"),
                    "content": turn_result.get("content", ""),
                    "model": turn_result.get("model_used", ""),
                    "tokens": turn_result.get("total_tokens", 0),
                })

            total_turns = len(condition.participants) * num_rounds
            result.num_turns, turn_error = await drive_turns(
                app, total_turns, record)

            # --- Conclude --- (a failed synthesis is an error, issue #71)
            result.conclusion, conclusion_error = await conclude(app)
            result.error = join_errors(turn_error, conclusion_error)

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
    output_path = Path(output_dir or Path(get_data_dir()) / RESULTS_DIRNAME)
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
        default=os.environ.get("EVAL_PROVIDER_URL", DEFAULT_PROVIDER_URL),
        help="OpenAI-compatible API base URL",
    )
    parser.add_argument(
        "--model", type=str,
        default=os.environ.get("EVAL_MODEL", DEFAULT_MODEL),
        help="Model name for all participants",
    )
    parser.add_argument(
        "--api-key-env", type=str,
        default=os.environ.get("EVAL_API_KEY_ENV", ""),
        help="Env var name holding the API key",
    )
    parser.add_argument(
        "--output", type=str, default="",
        help=f"Output directory for results (default: <app data dir>/{RESULTS_DIRNAME})",
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
        print("\nEvaluation configuration:")
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
    print("\nRun scoring with: python -m consensus.evaluation.scorer --input <results_dir>")


if __name__ == "__main__":
    main()
