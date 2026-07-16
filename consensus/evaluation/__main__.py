"""Entry point for: python -m consensus.evaluation

Dispatches to runner or scorer based on subcommand.
Usage:
    python -m consensus.evaluation run [OPTIONS]     # Run evaluation
    python -m consensus.evaluation score [OPTIONS]   # Score results
    python -m consensus.evaluation list              # List cases and conditions
"""

import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Subcommands:")
        print("  run    — Run evaluation (see: python -m consensus.evaluation run --help)")
        print("  score  — Score results (see: python -m consensus.evaluation score --help)")
        print("  list   — List available cases and conditions")
        sys.exit(0)

    subcmd = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip subcommand

    if subcmd == "run":
        from consensus.evaluation.runner import main as run_main
        run_main()
    elif subcmd == "score":
        from consensus.evaluation.scorer import main as score_main
        score_main()
    elif subcmd == "list":
        from consensus.evaluation.cases import CASES
        from consensus.evaluation.conditions import CONDITIONS

        print("\nCases:")
        for c in CASES:
            print(f"  {c.id}: {c.title} [{c.difficulty}] — {c.gold_diagnosis}")

        print(f"\nConditions:")
        for name, cond in CONDITIONS.items():
            parts = [cond.description]
            if cond.enable_da:
                parts.append("+DA")
            if cond.enable_memory:
                parts.append("+memory")
            if cond.enable_tools:
                parts.append("+tools")
            print(f"  {name}: {', '.join(parts)}")

        print(f"\nTotal: {len(CASES)} cases x {len(CONDITIONS)} conditions = "
              f"{len(CASES) * len(CONDITIONS)} runs")
    else:
        print(f"Unknown subcommand: {subcmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
