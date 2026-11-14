"""Scoring module for evaluation results.

Scores each run on:
1. Diagnostic accuracy (exact match against gold diagnosis + aliases)
2. Differential quality (is the gold diagnosis in the differential list?)
3. Reasoning quality (LLM-judged on a 1-5 rubric)

Usage:
    python -m consensus.evaluation.scorer --input <app data dir>/eval_results [OPTIONS]
    python -m consensus.evaluation.scorer --input <app data dir>/eval_results --llm-judge
"""

import argparse
import json
import logging
import os
import re
from pathlib import Path

from consensus.evaluation.constants import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER_URL,
    JUDGE_DIMENSIONS,
    JUDGE_MAX_SCORE,
    JUDGE_MAX_TOKENS,
    JUDGE_MIN_SCORE,
    JUDGE_TEMPERATURE,
    MIN_PARTIAL_MATCH_CHARS,
    MIN_PARTIAL_MATCH_WORDS,
)
from consensus.evaluation.report import print_report

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


def _acceptable_terms(gold_diagnosis: str, gold_aliases: list[str]) -> list[str]:
    """Lowercased gold diagnosis and aliases, minus any that normalise to "".

    A blank term is a substring of every text, so it would match anything.
    """
    terms = [gold_diagnosis.lower()] + [a.lower() for a in gold_aliases]
    return [t for t in terms if normalise(t)]


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
    all_acceptable = _acceptable_terms(gold_diagnosis, gold_aliases)

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
        if len(words) >= MIN_PARTIAL_MATCH_WORDS:
            # Check if the most distinctive word (longest) appears
            longest_word = max(words, key=len)
            if len(longest_word) >= MIN_PARTIAL_MATCH_CHARS and longest_word in norm_conclusion:
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
    all_acceptable = _acceptable_terms(gold_diagnosis, gold_aliases)

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
            if len(longest) >= MIN_PARTIAL_MATCH_CHARS and longest in content:
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


def judge_reply_problem(scores: object) -> str:
    """Why a parsed judge reply is unusable, or "" if it is valid.

    Valid means a JSON object with every ``JUDGE_DIMENSIONS`` key holding a
    number (not a bool) in ``JUDGE_MIN_SCORE..JUDGE_MAX_SCORE``.
    """
    if not isinstance(scores, dict):
        return "judge reply is not a JSON object"
    bad = [d for d in JUDGE_DIMENSIONS
           if isinstance(scores.get(d), bool)
           or not isinstance(scores.get(d), (int, float))
           or not JUDGE_MIN_SCORE <= scores[d] <= JUDGE_MAX_SCORE]
    if bad:
        return ("judge reply has missing or out-of-range scores for: "
                + ", ".join(bad))
    return ""


async def llm_judge_score(
    case_presentation: str,
    gold_diagnosis: str,
    conclusion: str,
    provider_url: str,
    model: str,
    api_key: str = "",
) -> dict:
    """Use an LLM to score the conclusion on a rubric.

    Returns ``{"scores": {...}, "error": ""}`` on success. On any failure
    (transport, unparseable or invalid reply) returns empty scores and a
    non-empty ``error``.
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
                temperature=JUDGE_TEMPERATURE,
                max_tokens=JUDGE_MAX_TOKENS,
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
        problem = judge_reply_problem(scores)
        if problem:
            raise ValueError(problem)
        return {"scores": scores, "error": ""}

    except Exception as e:
        logger.warning("LLM judge failed: %r", e)
        # Timeouts stringify to "", and callers treat a blank error as success.
        return {"scores": {}, "error": str(e) or type(e).__name__}


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------

def score_run(run_data: dict, case_presentation: str = "") -> dict:
    """Score a single run result. Returns the run data augmented with scores."""
    conclusion = run_data.get("conclusion", "")
    gold = run_data.get("gold_diagnosis", "")
    messages = run_data.get("messages", [])

    # Load aliases from cases module
    from consensus.evaluation.cases import get_case
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
                                model: str, api_key: str = "") -> dict | None:
    """Run the LLM judge on a completed run and store its scores.

    Returns the judge result (``{"scores", "error"}``), or None when the run
    is missing, not done, or its case is gone. A failed judgement is
    returned but not stored: storing replaces the run's previous
    ``llm_judge`` score, and an outage must not wipe a good one.
    """
    run = eval_db.get_run(run_id)
    if not run or run["status"] != "done":
        return None

    case = eval_db.get_case(run["case_id"])
    if not case:
        return None

    result = await llm_judge_score(
        case_presentation=case["presentation"],
        gold_diagnosis=case["gold_diagnosis"],
        conclusion=run.get("conclusion", ""),
        provider_url=provider_url,
        model=model,
        api_key=api_key,
    )
    if not result["error"]:
        eval_db.add_score(run_id, "llm_judge", result)
    return result


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
        default=os.environ.get("EVAL_PROVIDER_URL", DEFAULT_PROVIDER_URL),
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
        from consensus.evaluation.cases import get_case

        judge_model = args.judge_model or os.environ.get("EVAL_MODEL", DEFAULT_MODEL)
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
