"""Scoring module for evaluation results.

Scores each run on:
1. Diagnostic accuracy (exact match against gold diagnosis + aliases)
2. Differential quality (is the gold diagnosis in the differential list?)
3. Reasoning quality (LLM-judged on a 1-5 rubric)

Usage:
    python -m evaluation.scorer --input evaluation/results [OPTIONS]
    python -m evaluation.scorer --input evaluation/results --llm-judge
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# String-matching scorer (fast, no API calls)
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def score_diagnostic_accuracy(
    conclusion: str,
    gold_diagnosis: str,
    gold_aliases: list[str],
) -> dict:
    """Check if the gold diagnosis appears in the conclusion.

    Returns:
        dict with keys:
        - correct: bool — diagnosis mentioned in conclusion
        - matched_term: str — which alias matched (or "")
        - confidence: str — "exact" or "partial" or "none"
    """
    norm_conclusion = normalise(conclusion)
    all_acceptable = [gold_diagnosis.lower()] + [a.lower() for a in gold_aliases]

    for diag in all_acceptable:
        norm_diag = normalise(diag)
        if norm_diag in norm_conclusion:
            return {
                "correct": True,
                "matched_term": diag,
                "confidence": "exact",
            }

    # Partial matching: check if key words from the diagnosis appear
    # (e.g. "lupus" matches "systemic lupus erythematosus")
    for diag in all_acceptable:
        words = normalise(diag).split()
        if len(words) >= 2:
            # Check if the most distinctive word (longest) appears
            longest_word = max(words, key=len)
            if len(longest_word) >= 4 and longest_word in norm_conclusion:
                return {
                    "correct": True,
                    "matched_term": diag,
                    "confidence": "partial",
                }

    return {"correct": False, "matched_term": "", "confidence": "none"}


def score_differential_quality(
    messages: list[dict],
    gold_diagnosis: str,
    gold_aliases: list[str],
) -> dict:
    """Check whether the correct diagnosis appears anywhere in the discussion.

    This measures whether participants considered the right diagnosis
    at any point, even if the final conclusion was wrong.

    Returns:
        dict with:
        - mentioned: bool
        - first_mention_turn: int or -1
        - first_mention_speaker: str or ""
    """
    all_acceptable = [gold_diagnosis.lower()] + [a.lower() for a in gold_aliases]

    for i, msg in enumerate(messages):
        content = normalise(msg.get("content", ""))
        for diag in all_acceptable:
            if normalise(diag) in content:
                return {
                    "mentioned": True,
                    "first_mention_turn": i,
                    "first_mention_speaker": msg.get("speaker", ""),
                }
        # Also try key-word partial match
        for diag in all_acceptable:
            words = normalise(diag).split()
            longest = max(words, key=len) if words else ""
            if len(longest) >= 4 and longest in content:
                return {
                    "mentioned": True,
                    "first_mention_turn": i,
                    "first_mention_speaker": msg.get("speaker", ""),
                }

    return {"mentioned": False, "first_mention_turn": -1, "first_mention_speaker": ""}


# ---------------------------------------------------------------------------
# LLM-based judge (optional, requires API access)
# ---------------------------------------------------------------------------

LLM_JUDGE_PROMPT = """You are an expert medical evaluator. Given a clinical case and a discussion transcript with a final synthesis, evaluate the quality of the diagnostic reasoning.

## Clinical Case
{presentation}

## Correct Diagnosis
{gold_diagnosis}

## Discussion Conclusion
{conclusion}

## Evaluation Criteria

Rate each dimension from 1 (poor) to 5 (excellent):

1. **Diagnostic Accuracy** (1-5): Did the discussion arrive at the correct diagnosis?
   - 5: Correct diagnosis stated clearly and confidently
   - 4: Correct diagnosis stated but with unnecessary hedging
   - 3: Correct diagnosis mentioned but not as primary
   - 2: Correct diagnosis mentioned only in differential
   - 1: Correct diagnosis not mentioned

2. **Reasoning Quality** (1-5): How systematic and logical was the reasoning?
   - 5: Exemplary clinical reasoning, all key findings connected
   - 4: Good reasoning with minor gaps
   - 3: Adequate but superficial reasoning
   - 2: Reasoning present but flawed or disorganised
   - 1: Poor or absent reasoning

3. **Evidence Use** (1-5): Were key findings correctly identified and used?
   - 5: All key findings identified and correctly interpreted
   - 4: Most key findings identified
   - 3: Some key findings missed
   - 2: Major findings missed or misinterpreted
   - 1: Findings largely ignored

4. **Differential Quality** (1-5): Was the differential diagnosis appropriate?
   - 5: Comprehensive, well-reasoned differential with correct prioritisation
   - 4: Good differential, correct diagnosis well-ranked
   - 3: Adequate differential but missing important alternatives
   - 2: Narrow or poorly reasoned differential
   - 1: No meaningful differential generated

5. **Critical Analysis** (1-5): Were assumptions challenged and errors caught?
   - 5: Rigorous critical analysis, biases identified
   - 4: Good challenge of assumptions
   - 3: Some critical analysis present
   - 2: Minimal critical engagement
   - 1: No critical analysis

Respond in JSON format only:
{{"accuracy": N, "reasoning": N, "evidence": N, "differential": N, "critical_analysis": N, "comments": "brief explanation"}}
"""


async def llm_judge_score(
    case_presentation: str,
    gold_diagnosis: str,
    conclusion: str,
    provider_url: str,
    model: str,
    api_key: str = "",
) -> dict:
    """Use an LLM to score the conclusion on a rubric.

    Returns dict with scores (1-5) for each dimension, or error.
    """
    try:
        from consensus.ai_client import AIClient

        prompt = LLM_JUDGE_PROMPT.format(
            presentation=case_presentation,
            gold_diagnosis=gold_diagnosis,
            conclusion=conclusion,
        )

        async with AIClient(provider_url, api_key) as client:
            resp = await client.complete(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=0.1,  # Low temperature for consistent scoring
                max_tokens=512,
            )

        # Parse JSON from response
        content = resp.content.strip()
        # Handle markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        scores = json.loads(content)
        return {"scores": scores, "error": ""}

    except Exception as e:
        logger.warning("LLM judge failed: %s", e)
        return {"scores": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------

def score_run(run_data: dict, case_presentation: str = "") -> dict:
    """Score a single run result. Returns the run data augmented with scores."""
    conclusion = run_data.get("conclusion", "")
    gold = run_data.get("gold_diagnosis", "")
    messages = run_data.get("messages", [])

    # Load aliases from cases module
    from evaluation.cases import get_case
    case = get_case(run_data["case_id"])
    aliases = case.gold_aliases if case else []

    accuracy = score_diagnostic_accuracy(conclusion, gold, aliases)
    differential = score_differential_quality(messages, gold, aliases)

    run_data["scores"] = {
        "diagnostic_accuracy": accuracy,
        "differential_quality": differential,
    }
    return run_data


def score_all(results_dir: str) -> list[dict]:
    """Score all results in a directory."""
    results_path = Path(results_dir)
    combined_file = results_path / "all_results.json"

    if combined_file.exists():
        with open(combined_file) as f:
            runs = json.load(f)
    else:
        # Load individual run files
        runs = []
        for f in sorted(results_path.glob("eval-*.json")):
            with open(f) as fp:
                runs.append(json.load(fp))

    scored = [score_run(r) for r in runs]
    return scored


# ---------------------------------------------------------------------------
# DB-aware scoring (used by eval_routes.py)
# ---------------------------------------------------------------------------

def score_run_from_db(eval_db, run_id: int) -> None:
    """Score a completed run and write scores to the eval DB.

    Reads the run's conclusion and messages from eval_db, runs string-match
    scoring, and writes results to eval_run_scores.
    """
    run = eval_db.get_run(run_id)
    if not run or run["status"] != "done":
        return

    case = eval_db.get_case(run["case_id"])
    if not case:
        return

    conclusion = run.get("conclusion", "")
    gold = case["gold_diagnosis"]
    aliases = case.get("aliases", [])
    messages = eval_db.get_run_messages(run_id)

    # Diagnostic accuracy
    accuracy = score_diagnostic_accuracy(conclusion, gold, aliases)
    eval_db.add_score(run_id, "diagnostic_accuracy", accuracy)

    # Differential quality (was correct diagnosis mentioned anywhere?)
    msg_dicts = [{"content": m["content"], "speaker": m["speaker"]}
                 for m in messages]
    differential = score_differential_quality(msg_dicts, gold, aliases)
    eval_db.add_score(run_id, "differential_quality", differential)


async def llm_judge_run_from_db(eval_db, run_id: int, provider_url: str,
                                 model: str, api_key: str = "") -> None:
    """Run LLM judge on a completed run and write scores to eval DB."""
    run = eval_db.get_run(run_id)
    if not run or run["status"] != "done":
        return

    case = eval_db.get_case(run["case_id"])
    if not case:
        return

    result = await llm_judge_score(
        case_presentation=case["presentation"],
        gold_diagnosis=case["gold_diagnosis"],
        conclusion=run.get("conclusion", ""),
        provider_url=provider_url,
        model=model,
        api_key=api_key,
    )
    eval_db.add_score(run_id, "llm_judge", result)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(scored_results: list[dict]):
    """Print a summary table of evaluation results."""
    # Group by condition
    by_condition: dict[str, list[dict]] = {}
    for r in scored_results:
        cond = r.get("condition", "unknown")
        by_condition.setdefault(cond, []).append(r)

    print(f"\n{'='*80}")
    print("EVALUATION RESULTS SUMMARY")
    print(f"{'='*80}\n")

    # Header
    print(f"{'Condition':<25} {'N':>4} {'Accuracy':>10} {'In Diff':>10} "
          f"{'Tokens':>10} {'Errors':>8}")
    print("-" * 80)

    for cond_name in ["baseline", "multi_agent", "multi_agent_da",
                       "multi_agent_da_memory", "full"]:
        runs = by_condition.get(cond_name, [])
        if not runs:
            continue

        n = len(runs)
        correct = sum(
            1 for r in runs
            if r.get("scores", {}).get("diagnostic_accuracy", {}).get("correct", False)
        )
        in_diff = sum(
            1 for r in runs
            if r.get("scores", {}).get("differential_quality", {}).get("mentioned", False)
        )
        total_tokens = sum(r.get("total_tokens", 0) for r in runs)
        errors = sum(1 for r in runs if r.get("error"))

        acc_pct = f"{correct}/{n} ({100*correct/n:.0f}%)" if n else "—"
        diff_pct = f"{in_diff}/{n} ({100*in_diff/n:.0f}%)" if n else "—"

        print(f"{cond_name:<25} {n:>4} {acc_pct:>10} {diff_pct:>10} "
              f"{total_tokens:>10,} {errors:>8}")

    print()

    # Per-case breakdown
    print(f"\n{'='*80}")
    print("PER-CASE BREAKDOWN")
    print(f"{'='*80}\n")

    # Group by case
    by_case: dict[str, list[dict]] = {}
    for r in scored_results:
        by_case.setdefault(r["case_id"], []).append(r)

    for case_id in sorted(by_case.keys()):
        runs = by_case[case_id]
        case_title = runs[0].get("case_title", case_id)
        gold = runs[0].get("gold_diagnosis", "")
        print(f"\n{case_id}: {case_title}")
        print(f"  Gold: {gold}")

        for r in runs:
            cond = r["condition"]
            acc = r.get("scores", {}).get("diagnostic_accuracy", {})
            correct = "CORRECT" if acc.get("correct") else "WRONG"
            matched = acc.get("matched_term", "")
            conf = acc.get("confidence", "")
            tokens = r.get("total_tokens", 0)
            err = r.get("error", "")

            status = f"{correct}"
            if matched:
                status += f" ({conf}: '{matched}')"
            if err:
                status = f"ERROR: {err[:50]}"

            print(f"  {cond:<25} {status:<40} [{tokens:,} tokens]")

    # LLM judge scores if present
    has_llm_scores = any(
        r.get("scores", {}).get("llm_judge")
        for r in scored_results
    )
    if has_llm_scores:
        print(f"\n{'='*80}")
        print("LLM JUDGE SCORES (1-5 scale)")
        print(f"{'='*80}\n")

        dims = ["accuracy", "reasoning", "evidence", "differential", "critical_analysis"]
        print(f"{'Condition':<25}", end="")
        for d in dims:
            print(f" {d[:10]:>10}", end="")
        print()
        print("-" * 80)

        for cond_name in ["baseline", "multi_agent", "multi_agent_da",
                           "multi_agent_da_memory", "full"]:
            runs = by_condition.get(cond_name, [])
            judged = [
                r for r in runs
                if r.get("scores", {}).get("llm_judge", {}).get("scores")
            ]
            if not judged:
                continue

            print(f"{cond_name:<25}", end="")
            for d in dims:
                vals = [
                    r["scores"]["llm_judge"]["scores"].get(d, 0)
                    for r in judged
                ]
                avg = sum(vals) / len(vals) if vals else 0
                print(f" {avg:>10.1f}", end="")
            print(f"  (n={len(judged)})")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score evaluation results")
    parser.add_argument(
        "--input", type=str, required=True,
        help="Directory containing result JSON files",
    )
    parser.add_argument(
        "--llm-judge", action="store_true",
        help="Also run LLM-based judge scoring (requires API access)",
    )
    parser.add_argument(
        "--judge-provider-url", type=str,
        default=os.environ.get("EVAL_PROVIDER_URL", "http://localhost:11434/v1"),
        help="Provider URL for the LLM judge",
    )
    parser.add_argument(
        "--judge-model", type=str,
        default=os.environ.get("EVAL_JUDGE_MODEL", ""),
        help="Model for LLM judge (defaults to EVAL_MODEL)",
    )
    parser.add_argument(
        "--judge-api-key-env", type=str,
        default=os.environ.get("EVAL_API_KEY_ENV", ""),
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="Output file for scored results JSON",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Score with string matching
    scored = score_all(args.input)
    logger.info("Scored %d runs with string matching", len(scored))

    # Optionally run LLM judge
    if args.llm_judge:
        import asyncio
        from evaluation.cases import get_case

        judge_model = args.judge_model or os.environ.get("EVAL_MODEL", "llama3")
        api_key = os.environ.get(args.judge_api_key_env, "") if args.judge_api_key_env else ""

        async def judge_all():
            for i, r in enumerate(scored):
                case = get_case(r["case_id"])
                if not case:
                    continue
                logger.info(
                    "LLM judging %d/%d: %s / %s",
                    i + 1, len(scored), r["case_id"], r["condition"],
                )
                judge_result = await llm_judge_score(
                    case_presentation=case.presentation,
                    gold_diagnosis=case.gold_diagnosis,
                    conclusion=r.get("conclusion", ""),
                    provider_url=args.judge_provider_url,
                    model=judge_model,
                    api_key=api_key,
                )
                r["scores"]["llm_judge"] = judge_result

        asyncio.run(judge_all())
        logger.info("LLM judge scoring complete")

    # Save scored results
    output_file = args.output or str(Path(args.input) / "scored_results.json")
    with open(output_file, "w") as f:
        json.dump(scored, f, indent=2)
    logger.info("Scored results saved to %s", output_file)

    # Print report
    print_report(scored)


if __name__ == "__main__":
    main()
