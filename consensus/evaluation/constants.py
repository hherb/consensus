"""Shared constants for the evaluation framework (golden rule 3)."""

#: Provider and model the CLI runner and scorer fall back to when neither a
#: flag nor ``EVAL_PROVIDER_URL`` / ``EVAL_MODEL`` is set (a local Ollama).
DEFAULT_PROVIDER_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3"

#: The rubric dimensions ``scorer.LLM_JUDGE_PROMPT`` asks the judge to rate
#: 1-5, in report column order. Keep in step with the JSON keys in that
#: prompt and ``JUDGE_DIMENSIONS`` in ``static/eval_scoring.js``
#: (``tests/test_eval_judge_robustness.py`` checks both).
JUDGE_DIMENSIONS = ("accuracy", "reasoning", "evidence", "differential",
                    "critical_analysis")

#: Inclusive range of a valid rubric score; a judge reply with any
#: dimension missing or outside it is a failed judgement.
JUDGE_MIN_SCORE = 1
JUDGE_MAX_SCORE = 5

#: Low, so judging the same run twice gives consistent scores.
JUDGE_TEMPERATURE = 0.1

#: Enough for the five integer scores plus a brief comment.
JUDGE_MAX_TOKENS = 512

#: ``score_diagnostic_accuracy`` falls back to a term's longest word only
#: when the term has at least this many words (``score_differential_quality``
#: always falls back) ...
MIN_PARTIAL_MATCH_WORDS = 2
#: ... and that word is at least this long, so "of" or "the" never match.
MIN_PARTIAL_MATCH_CHARS = 4

#: Width of the CLI report's rules and tables.
REPORT_WIDTH = 80

#: How much of a run's error text the per-case breakdown shows.
REPORT_ERROR_PREVIEW_CHARS = 50

#: Allowed ``cases.difficulty`` values — mirrors the ``CHECK`` constraint in
#: ``evaluation/migrations/001_baseline.sql``.
CASE_DIFFICULTIES = frozenset({"easy", "moderate", "hard"})

#: Allowed ``condition_participants.role`` values — mirrors the ``CHECK``
#: constraint in ``evaluation/migrations/001_baseline.sql``.
PARTICIPANT_ROLES = frozenset({"standard", "devils_advocate"})

#: Largest value an SQLite INTEGER column holds; a bigger JSON integer
#: makes ``sqlite3`` raise ``OverflowError``, so request checks reject it.
SQLITE_MAX_INTEGER = 2**63 - 1
