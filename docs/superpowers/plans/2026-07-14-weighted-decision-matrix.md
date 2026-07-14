# Weighted Decision Matrix (MCDA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #25 — a Weighted Decision Matrix (multi-criteria decision analysis) discussion method: enumerate options → jointly define weighted criteria → score every option × criterion → deterministic sensitivity analysis → a structured, machine-readable decision artifact that the storyboard, MCP server, or a follow-up discussion can consume.

**Architecture:** One new `DiscussionMethod` (`consensus/methods/decision_matrix.py`) assembled from five new composable `PhaseHandler`s in `consensus/methods/phases/`, backed by a shared pure-function helper module `_mcda_helpers.py` (mirrors `_ngt_helpers.py`). It generalises the two handlers named in the issue: `define_criteria` (adversarial-collab) becomes a weighted-criteria phase, and `evaluate_matrix` (ACH) becomes an option×criterion scoring phase. Four phases force structured output tools per the issue-#23 pattern (`submit_options`, `submit_weighted_criteria`, `submit_scores`, `submit_decision`); free-text `process_response` paths remain as the human/fallback layer. All numeric analysis (weighted totals, per-participant divergence, one-at-a-time sensitivity) is computed deterministically in pure helper functions — the moderator only *interprets* computed numbers, never produces them.

**Tech Stack:** Python 3 (stdlib only), pytest, `uv` for environment management.

## Global Constraints

- **`uv` only** — never call `pip` directly. Tests run with `uv run pytest …`.
- **TDD** — each task writes the failing test first, verifies it fails, implements, verifies it passes, commits.
- **Docstrings and type hints mandatory** on every function/method (`docs/llm/golden_rules.md`).
- **No magic numbers** — all thresholds/caps are module constants.
- **Files under ~500 lines.**
- **HANDOVER.md conventions (2026-07-14):**
  - Structured-phase conversions keep `process_response` (humans type free text; structured path falls back after exhausted retries).
  - Every parse-gated phase needs a give-up cap (`MAX_*` constants) that logs a warning when tripped.
  - Structured tools include a required `reasoning` field rendered before the data display (exception here: `submit_decision` uses its required `rationale` field in that role, like `submit_claims`' `preliminary_conclusion`).
  - Never derive a phase turn order by filtering the incoming `entity_ids`; moderator-only phases return `[discussion.moderator_id]`.
  - Structured items are `.strip().rstrip('.')`-normalised to match the regex paths.
  - Dynamic-key maps (scores keyed option-label × criterion-label) declare `additionalProperties` (the `MATRIX_TOOL_PARAMETERS` pattern).
  - Moderator captures happen in real moderator turns (`get_turn_order -> [moderator_id]`), never in summaries.
- **Branch:** work happens on the current worktree branch `claude/handover-instructions-377332`; commit after each task.

## Design decisions

- **Method name** `decision_matrix`, class `WeightedDecisionMatrix`, display name "Weighted Decision Matrix (MCDA)".
- **Labels:** options are `O1…On`, criteria `C1…Cm` (1-based ids, mirroring ACH's `H`/`E` labels).
- **Weights** are integers 1–5 proposed per participant per criterion; the *effective* weight is the arithmetic mean of each participant's most recent vote (a refinement-round resubmission replaces that participant's earlier vote — dict keyed by entity id).
- **Scores** are integers 1–5 per option × criterion per participant. Aggregation uses the mean of submitted scores per cell; a cell nobody scored defaults to the scale midpoint `DEFAULT_SCORE = 3` (partial coverage is allowed, matching the ACH matrix precedent).
- **Weighted total** per option = Σ over criteria of (mean weight × mean score).
- **Divergence** per option = max − min of per-participant weighted totals (each participant's own scores, group weights; 0.0 with fewer than two scorers).
- **Sensitivity** is one-at-a-time: for each criterion, recompute the winner with that criterion *excluded* (weight 0) and with its weight *doubled*; any variation that flips the winner marks the criterion "pivotal". A top-two margin within `CLOSE_CALL_MARGIN = 0.05` of the top total flags a close call.
- **Decision artifact** (`method_state["decision_artifact"]`): JSON-serialisable dict with the ranked options (weighted totals + per-criterion mean scores), effective criteria weights, divergence, sensitivity report, scorer count, the recommendation (option id + text), rationale, and caveats. The free-text fallback always records an artifact too (defaulting the recommendation to the top-ranked option, with a caveat noting the default).
- **Aborts:** zero options after `MAX_OPTIONS_ROUNDS` aborts the method (generate_ideas pattern); zero criteria after `MAX_CRITERIA_ROUNDS` likewise — scoring is impossible without either. Later phases carry defensive degenerate guards (`get_output_tool -> None`) per the evaluate_matrix pattern.
- **No anonymisation:** unlike NGT/Delphi, MCDA benefits from participants seeing each other's options and criteria to complement them.

## File Structure

| File | Responsibility |
|------|----------------|
| `consensus/methods/phases/_mcda_helpers.py` (create) | Constants, JSON Schemas, payload validators, record functions, aggregation/sensitivity/artifact math, display formatting |
| `consensus/methods/phases/enumerate_options.py` (create) | Phase 1 handler: option enumeration (`submit_options`), abort-on-no-options |
| `consensus/methods/phases/weight_criteria.py` (create) | Phase 2 handler: weighted criteria (`submit_weighted_criteria`), abort-on-no-criteria |
| `consensus/methods/phases/score_options.py` (create) | Phase 3 handler: option×criterion scoring (`submit_scores`) |
| `consensus/methods/phases/analyse_sensitivity.py` (create) | Phase 4 handler: moderator-only presentation of computed sensitivity |
| `consensus/methods/phases/decide.py` (create) | Phase 5 handler: moderator-only decision capture (`submit_decision`) + artifact assembly |
| `consensus/methods/decision_matrix.py` (create) | `WeightedDecisionMatrix` method assembly + conclusion prompt |
| `consensus/methods/__init__.py` (modify) | Register `"decision_matrix"` |
| `consensus/methods/recommender.py` (modify) | `_TAXONOMY` line for MCDA |
| `tests/test_mcda_helpers.py` (create) | Helper-module unit tests |
| `tests/test_phases_mcda.py` (create) | Handler prompts/free-text/advancement/abort/method-level tests |
| `tests/test_mcda_structured.py` (create) | Structured-output conversion tests (per-#23 convention) |
| `docs/devel/15-discussion-methods.md` (modify) | File list + method table |
| `docs/user_manual/05_discussion_methods.md` (modify) | Method section + "Choosing a Method" row |
| `HANDOVER.md` (modify) | Mark #25 done; record follow-ups |

**Method state keys** (contributed by handler `init_state`, no collisions):
- `options: list[dict]` — `{"id": int (1-based), "entity_id": int, "entity_name": str, "text": str}` (EnumerateOptionsHandler)
- `criteria: list[dict]` — `{"id": int (1-based), "name": str, "weight_votes": dict[str, int]}` keyed by `str(entity_id)` (WeightCriteriaHandler)
- `scores: dict[str, dict[str, dict[str, int]]]` — `scores[str(entity_id)]["O1"]["C2"] = int` (ScoreOptionsHandler)
- `decision_artifact: dict` — written by DecideHandler (both paths)

---

### Task 1: MCDA helper module — recording & validation half

**Files:**
- Create: `consensus/methods/phases/_mcda_helpers.py`
- Test: `tests/test_mcda_helpers.py`

**Interfaces:**
- Consumes: `consensus.methods.parsing.{extract_json_block, parse_numbered_list, word_overlap_similar}`
- Produces (used by Tasks 2–6): constants `WEIGHT_MIN=1, WEIGHT_MAX=5, DEFAULT_WEIGHT=3, SCORE_MIN=1, SCORE_MAX=5, DEFAULT_SCORE=3, MIN_OPTION_LENGTH=3, MIN_CRITERION_LENGTH=3, SIMILARITY_THRESHOLD=0.7, MAX_OPTIONS_ROUNDS=3, MAX_CRITERIA_ROUNDS=4, CLOSE_CALL_MARGIN=0.05`; schemas `OPTIONS_TOOL_PARAMETERS, CRITERIA_TOOL_PARAMETERS, SCORES_TOOL_PARAMETERS, DECISION_TOOL_PARAMETERS`; functions `validate_options_payload(payload) -> str`, `record_options(state, entity, texts) -> list[dict]`, `validate_criteria_payload(payload) -> str`, `record_criteria(state, entity, items) -> list[dict]`, `extract_weighted_criteria(content) -> list[dict]`, `criterion_weight(criterion) -> float`, `option_label(option_id) -> str`, `criterion_label(criterion_id) -> str`, `validate_scores_payload(payload, options, criteria) -> str`, `record_scores(state, entity, scores) -> int`, `extract_scores(content) -> dict`, `validate_decision_payload(payload, valid_ids) -> str`

Steps: write failing tests (validator acceptance/rejection incl. `null` reasoning, boolean weights/scores, out-of-range values, unknown labels; recording dedup/merge semantics; free-text extraction) → verify fail → implement → verify pass → commit `feat(mcda): recording/validation helpers for Weighted Decision Matrix (#25)`.

### Task 2: MCDA helper module — aggregation, sensitivity, artifact & formatting half

Same files. Produces: `mean_scores(state) -> dict[str, dict[str, float]]`, `weighted_totals(state) -> dict[int, float]`, `participant_totals(state) -> dict[str, dict[int, float]]`, `divergence_by_option(state) -> dict[int, float]`, `ranked_options(state) -> list[dict]`, `sensitivity_report(state) -> dict`, `build_decision_artifact(state, recommended_option_id, rationale, caveats) -> dict`, formatters `format_options, format_criteria, format_score_table(scores, state), format_mean_score_matrix, format_weighted_ranking, format_divergence, format_sensitivity, format_decision_artifact`.

Key semantics to test: unscored cell → `DEFAULT_SCORE`; ties broken by lower option id; divergence 0.0 with <2 scorers; sensitivity flags pivotal criteria on both "excluded" and "doubled" variations; close-call margin; artifact is JSON-serialisable (`json.dumps` round-trip) and stored in `state["decision_artifact"]`.

Commit: `feat(mcda): aggregation, sensitivity and decision-artifact helpers (#25)`.

### Task 3: EnumerateOptionsHandler (`enumerate_options.py`)

Phase `options` ("Option Enumeration"), `rounds=1`, all participants, `submit_options` forced tool. `process_response` parses numbered lists (`parse_numbered_list(content, min_length=MIN_OPTION_LENGTH)`) → `record_options`. `should_advance`: options recorded and `phase_round > 1`, give-up at `MAX_OPTIONS_ROUNDS` (warn). `next_phase` returns `None` (abort) when the give-up tripped with zero options; `get_method_complete_message` explains. Mirrors `generate_ideas.py` minus anonymisation. Tests in `tests/test_phases_mcda.py`. Commit: `feat(mcda): option enumeration phase handler (#25)`.

### Task 4: WeightCriteriaHandler (`weight_criteria.py`)

Phase `criteria` ("Criteria & Weights"), `rounds=2` (propose, refine), all participants, `submit_weighted_criteria` forced tool. Free-text path: `extract_weighted_criteria` (numbered list with optional `(weight: N)` suffix; missing weight → `DEFAULT_WEIGHT`). Resubmission in the refinement round replaces that participant's weight vote. Give-up at `MAX_CRITERIA_ROUNDS`; abort (`next_phase -> None` + message) when zero criteria. Commit: `feat(mcda): weighted criteria phase handler (#25)`.

### Task 5: ScoreOptionsHandler (`score_options.py`)

Phase `score` ("Scoring"), `rounds=1`, all participants, `submit_scores` forced tool with two-level `additionalProperties` schema (the `MATRIX_TOOL_PARAMETERS` pattern). Degenerate guard: `get_output_tool -> None` and qualitative prompts when options or criteria are missing. Free-text path: `extract_scores` (fenced JSON, then balanced-brace inline scan) → `record_scores`. `process_structured_response` stores scores and renders the participant's own score table plus reasoning. `should_advance`: `phase_round > 1`. Commit: `feat(mcda): option-scoring phase handler (#25)`.

### Task 6: SensitivityHandler (`analyse_sensitivity.py`)

Phase `sensitivity` ("Sensitivity Analysis"), `rounds=1`, **moderator-only** (`get_turn_order -> [discussion.moderator_id]`), presentational — no structured tool (the `rank_ideas.py` pattern). System prompt embeds computed `format_weighted_ranking`, `format_mean_score_matrix`, `format_divergence`, `format_sensitivity`; the moderator interprets robustness, pivotal criteria, close calls, and divergence. Transition message shows the weighted ranking. Commit: `feat(mcda): moderator sensitivity-analysis phase handler (#25)`.

### Task 7: DecideHandler (`decide.py`)

Phase `decide` ("Decision"), `rounds=1`, **moderator-only**, `submit_decision` forced tool (`recommended_option_id` int + required `rationale` + optional `caveats`; the rationale plays the `reasoning` role, like `submit_claims`' `preliminary_conclusion`). `validate_output` → `validate_decision_payload` (id must be a real option; bool rejected). `process_structured_response` → `build_decision_artifact` + `format_decision_artifact` display. Free-text fallback records an artifact too: recommendation defaults to the top-ranked option, rationale = the moderator's text, plus a caveat noting the default — so the artifact always exists. `get_output_tool -> None` when there are no options (degenerate). Commit: `feat(mcda): decision-capture phase handler with structured artifact (#25)`.

### Task 8: Method assembly, registry, recommender

`consensus/methods/decision_matrix.py`: `WeightedDecisionMatrix` with the five handlers; `get_conclusion_prompt` embeds `format_decision_artifact` (or the computed ranking when no artifact) and asks for decision / rationale / runner-up analysis / divergence / robustness / caveats. Register in `consensus/methods/__init__.py` (`_METHODS`, import, `__all__`). Add `_TAXONOMY` line in `recommender.py`: `- Decision-making by scoring options against weighted criteria → Weighted Decision Matrix (MCDA)`. Method-level tests (registry, `init_state` merge, `requires_structured_output()` True, phase order, conclusion prompt content). Commit: `feat(mcda): assemble and register Weighted Decision Matrix (#25)`.

### Task 9: Structured-output conversion coverage (`tests/test_mcda_structured.py`)

Per the #23 convention (mirror `tests/test_ngt_structured.py`): flags (`options`/`criteria`/`score`/`decide` require structured output; `sensitivity` does not), tool specs (names, required fields, schema shapes, degenerate `None` cases), prompts name their tools, and structured vs free-text paths produce equivalent state. Commit: `test(mcda): structured-output conversion coverage (#25, #23 convention)`.

### Task 10: Docs, HANDOVER, full suite, PR

Update `docs/devel/15-discussion-methods.md` (file list + method table) and `docs/user_manual/05_discussion_methods.md` (method section + "Choosing a Method" row). Update `HANDOVER.md`: mark #25 done, promote #27/#26 in next steps, record follow-ups. Run the full suite (`uv run pytest`), then commit and open the PR referencing #25. Commit: `docs: document Weighted Decision Matrix; update handover (#25)`.

## Self-Review notes

- Issue coverage: options (Task 3), criteria & weights generalising `define_criteria` (Task 4), scoring generalising `evaluate_matrix` (Task 5), sensitivity (Task 6, computed deterministically), structured decision output with ranked options + per-participant divergence + recorded rationale (Tasks 2 & 7). "Make a decision between options" recommender fit (Task 8 taxonomy line).
- Types consistent: option/criterion label helpers shared by validators, aggregation, and formatters; `scores` shape identical on both paths.
- Full code for each task lives in the session transcript / final implementation; this plan intentionally references the established repo patterns (`generate_ideas.py`, `cluster_ideas.py`, `evaluate_matrix.py`, `allocate_points.py`, `rank_ideas.py`) that each handler mirrors, rather than duplicating ~2500 lines — the patterns are normative and already merged.
