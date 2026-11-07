"""Plain-text summary report for scored evaluation results.

Split out of ``scorer.py`` (issue #61); ``scorer.main`` prints it after
scoring. Pure presentation — it reads the ``scores`` dicts the scorer adds
and writes to stdout.
"""

from consensus.evaluation.conditions import CONDITIONS
from consensus.evaluation.constants import (
    JUDGE_DIMENSIONS,
    REPORT_ERROR_PREVIEW_CHARS,
    REPORT_WIDTH,
)


def print_report(scored_results: list[dict]) -> None:
    """Print a summary table of evaluation results."""
    # Group by condition
    by_condition: dict[str, list[dict]] = {}
    for r in scored_results:
        cond = r.get("condition", "unknown")
        by_condition.setdefault(cond, []).append(r)

    print(f"\n{'=' * REPORT_WIDTH}")
    print("EVALUATION RESULTS SUMMARY")
    print(f"{'=' * REPORT_WIDTH}\n")

    # Header
    print(f"{'Condition':<25} {'N':>4} {'Accuracy':>10} {'In Diff':>10} "
          f"{'Tokens':>10} {'Errors':>8}")
    print("-" * REPORT_WIDTH)

    for cond_name in CONDITIONS:
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
    print(f"\n{'=' * REPORT_WIDTH}")
    print("PER-CASE BREAKDOWN")
    print(f"{'=' * REPORT_WIDTH}\n")

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
                status = f"ERROR: {err[:REPORT_ERROR_PREVIEW_CHARS]}"

            print(f"  {cond:<25} {status:<40} [{tokens:,} tokens]")

    # LLM judge scores if present
    has_llm_scores = any(
        r.get("scores", {}).get("llm_judge")
        for r in scored_results
    )
    if has_llm_scores:
        print(f"\n{'=' * REPORT_WIDTH}")
        print("LLM JUDGE SCORES (1-5 scale)")
        print(f"{'=' * REPORT_WIDTH}\n")

        print(f"{'Condition':<25}", end="")
        for d in JUDGE_DIMENSIONS:
            print(f" {d[:10]:>10}", end="")
        print()
        print("-" * REPORT_WIDTH)

        for cond_name in CONDITIONS:
            runs = by_condition.get(cond_name, [])
            judged = [
                r for r in runs
                if r.get("scores", {}).get("llm_judge", {}).get("scores")
            ]
            if not judged:
                continue

            print(f"{cond_name:<25}", end="")
            for d in JUDGE_DIMENSIONS:
                vals = [
                    r["scores"]["llm_judge"]["scores"].get(d, 0)
                    for r in judged
                ]
                avg = sum(vals) / len(vals) if vals else 0
                print(f" {avg:>10.1f}", end="")
            print(f"  (n={len(judged)})")

    print()
