"""Shared constants for the evaluation framework (golden rule 3)."""

#: Provider and model the CLI runner and scorer fall back to when neither a
#: flag nor ``EVAL_PROVIDER_URL`` / ``EVAL_MODEL`` is set (a local Ollama).
DEFAULT_PROVIDER_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3"

#: The rubric dimensions ``scorer.LLM_JUDGE_PROMPT`` asks the judge to rate
#: 1-5, in report column order. Keep in step with ``JUDGE_DIMENSIONS`` in
#: ``static/eval_scoring.js``.
JUDGE_DIMENSIONS = ("accuracy", "reasoning", "evidence", "differential",
                    "critical_analysis")

#: Low, so judging the same run twice gives consistent scores.
JUDGE_TEMPERATURE = 0.1

#: Enough for the five integer scores plus a brief comment.
JUDGE_MAX_TOKENS = 512

#: String matching falls back to a term's longest word only when the term
#: has at least this many words ...
MIN_PARTIAL_MATCH_WORDS = 2
#: ... and that word is at least this long, so "of" or "the" never match.
MIN_PARTIAL_MATCH_CHARS = 4

#: Width of the CLI report's rules and tables.
REPORT_WIDTH = 80

#: How much of a run's error text the per-case breakdown shows.
REPORT_ERROR_PREVIEW_CHARS = 50
