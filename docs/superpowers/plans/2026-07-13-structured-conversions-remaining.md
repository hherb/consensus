# Remaining Structured-Output Conversions (#23) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the remaining regex-parsing method phases to forced tool-call
structured output, following the mechanism merged in PR #38, and close the two
known capability-check gaps (triage method switch, method recommender).

**Architecture:** No new mechanism. Each phase handler declares
`requires_structured_output = True`, returns an `OutputToolSpec` from
`get_output_tool`, validates payloads in `validate_output`, and records state in
`process_structured_response` — exactly like the three merged exemplars:
`consensus/methods/phases/estimate.py`, `vote.py`, and `frame_hypotheses.py`
(see also `revise_delphi.py`). The regex `process_response` path stays as the
human-input / containment fallback. Shared JSON Schemas and record helpers live
in the method's `_*_helpers.py` module.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio. No new dependencies.

## Global Constraints

- `uv` only, never pip (CLAUDE.md).
- TDD: failing test first for every behavior change (docs/llm/golden_rules.md).
- Files under ~500 lines; docstrings + type hints mandatory; no magic numbers
  (named module-level constants with a `#:` comment).
- **Keep `process_response` on every converted handler** — humans type free
  text and the structured path falls back to it after exhausted retries
  (HANDOVER.md conventions).
- Rewrite `get_system_prompt` / `get_turn_prompt` format instructions
  (JSON-block / numbered-list / tag mandates) to instruct calling the tool by
  name instead. Keep all non-format prompt content (context listings, role
  guidance) unchanged.
- `process_structured_response` must write **exactly the same
  `method_state` keys and value shapes** the existing regex path writes —
  downstream phases and synthesis helpers consume them.
- Never derive a phase turn order from incoming `entity_ids` by filtering the
  current order; filter the full roster (HANDOVER.md).
- Update any existing tests that assert the removed format wording; new
  structured tests follow the style of `tests/test_delphi_structured.py` /
  `tests/test_belief_framing_structured.py` (direct handler tests: declares
  tool, validates payloads, records state, free-text path still works).
- Run the covering test files during the task and the full suite
  (`uv run pytest tests/ -q`) before each commit.

**Note:** `tally.py` (listed in HANDOVER) needs **no conversion** — its
`process_response` is the default no-op and its prompts are empty; the tally is
computed by `_voting_helpers.tally_votes` at conclusion time. Task 11 records
this in HANDOVER.md.

---

### Task 1: Belief Diffusion `prior_beliefs` + `diffuse_beliefs` (`submit_beliefs`)

**Files:** modify `consensus/methods/phases/_belief_helpers.py`,
`prior_beliefs.py`, `diffuse_beliefs.py`; create
`tests/test_belief_distributions_structured.py`.

- Add to `_belief_helpers.py`: `BELIEFS_TOOL_PARAMETERS` (object with a
  required `beliefs` object property mapping hypothesis text to a 0.0–1.0
  probability, plus an optional `reasoning` string) and a shared
  `validate_beliefs_payload(payload, hypotheses) -> str` helper: `beliefs`
  must be a non-empty object; every key must exactly match one of the framed
  `hypotheses` (name the mismatched key and list valid ones in the error);
  every value numeric in [0, 1]; require a belief for **every** hypothesis.
  Add a shared `record_beliefs(state, entity, round_num, beliefs)` helper if
  the two handlers would otherwise duplicate the append logic.
- Both handlers: `requires_structured_output = True`; `get_output_tool`
  returns `submit_beliefs` (description names the current hypotheses);
  `validate_output` delegates to the shared helper;
  `process_structured_response` appends the same
  `{"round", "entity_id", "entity_name", "beliefs"}` entry to
  `belief_history` that the regex path writes (round 0 for prior,
  `diffuse_round + 1` for diffuse) and renders the same belief-bar display
  the free-text path shows (reuse `format_belief_bar`).
- Edge case: if `state["hypotheses"]` is empty (framing aborted),
  `get_output_tool` must return `None` so the turn falls through to the
  free-text path instead of forcing an unsatisfiable tool call (pattern:
  `vote.py` returning `None` with no pending motions).
- Rewrite the JSON-block prompt paragraphs in both handlers to name
  `submit_beliefs`.
- Covering tests: new file + `tests/test_phases_belief_diffusion.py`,
  `tests/test_moderator_framing_phases.py`.

Commit: `feat(belief-diffusion): forced submit_beliefs tool for prior/diffuse phases (#23)`

---

### Task 2: Self-Distillation `blind_evaluate` (`submit_validity_scores`)

**Files:** modify `consensus/methods/phases/_distillation_helpers.py`,
`blind_evaluate.py`; create `tests/test_blind_evaluate_structured.py`.

- Add `VALIDITY_TOOL_PARAMETERS` to `_distillation_helpers.py`: required
  `scores` array of `{inference_id: string, score: integer 1–5}` plus required
  `overall` integer 1–5.
- `BlindEvaluateHandler`: `requires_structured_output = True`;
  `get_output_tool` returns `None` when `state.get("skeleton")` is missing/
  empty (extraction gave up — free-text discussion happens instead), else
  `submit_validity_scores` whose description lists the inference/conclusion
  IDs to score; `validate_output` rejects unknown IDs (name them and list
  valid IDs), scores outside 1–5, missing `overall`, and missing IDs (every
  scorable inference+conclusion must be scored);
  `process_structured_response` writes the same
  `validity_scores[inference_id][entity_name] = score` and
  `overall_scores[entity_name]` shapes and renders a display equivalent to
  the tag format (keep `[VALIDITY id: n]` tags in the display content so the
  existing `filter_context_message` blindness filter still recognizes
  evaluation messages — verify against its implementation).
- Rewrite the EXACT-tags prompt instructions to name the tool. Keep
  `filter_context_message` and the turn-order override untouched.
- Covering tests: new file + `tests/test_phases_self_distillation.py`.

Commit: `feat(self-distillation): forced submit_validity_scores tool for blind evaluation (#23)`

---

### Task 3: Adversarial Collab `define_criteria` (`submit_criteria`)

**Files:** modify `consensus/methods/phases/define_criteria.py`; create
`tests/test_define_criteria_structured.py`.

- `submit_criteria`: required `criteria` array of strings (each a complete,
  testable criterion). `validate_output`: non-empty list; each item a
  substantive string (reuse the handler's existing >10-char filter as a named
  constant if not already one).
- `process_structured_response`: dedup against existing
  `state["criteria"]` exactly as the regex path does (exact-membership check)
  and append; display the accepted criteria as a numbered list plus the
  original count footer if the regex path renders one.
- Keep `MAX_CRITERIA_ROUNDS` give-up logic untouched.
- Covering tests: new file + `tests/test_phases_adversarial_collab.py`,
  `tests/test_moderator_framing_phases.py`.

Commit: `feat(adversarial-collab): forced submit_criteria tool (#23)`

---

### Task 4: ACH `evaluate_matrix` (`submit_matrix_ratings`)

**Files:** modify `consensus/methods/phases/evaluate_matrix.py`; create
`tests/test_evaluate_matrix_structured.py`.

- `submit_matrix_ratings`: required `ratings` object — hypothesis label →
  object of evidence label → rating string. JSON Schema uses
  `additionalProperties` for the dynamic keys with the rating value
  constrained to the enum the regex path accepts (check `_parse_ratings` /
  downstream `analyse_ach.py` for the accepted symbols — "+", "-", "0" and
  any variants like "++"/"--" the existing code tolerates; match it exactly).
- `validate_output`: hypothesis keys must match the framed hypothesis labels
  and evidence keys the recorded evidence labels (name mismatches, list valid
  labels); every rating in the accepted set; require complete coverage only
  if the regex path requires it — otherwise accept partial matrices exactly
  like the current parser does.
- `process_structured_response`: write
  `state["matrix"][str(entity.id)] = ratings` (same shape) and render the
  same matrix display the free-text path shows.
- Covering tests: new file + `tests/test_phases_ach.py`,
  `tests/test_small_method_defects.py`.

Commit: `feat(ach): forced submit_matrix_ratings tool for the evaluate phase (#23)`

---

### Task 5: Self-Distillation `distill_skeleton` (`submit_skeleton`, moderator turn)

**Files:** modify `consensus/methods/phases/_distillation_helpers.py`,
`distill_skeleton.py`; create `tests/test_distill_skeleton_structured.py`.

- `SKELETON_TOOL_PARAMETERS` in `_distillation_helpers.py`: required
  `premises` / `inferences` / `conclusions` arrays (items: `{id, text}` plus
  `from` id-array on inferences/conclusions) and a required
  `rich_summary` string (replaces the `RICH SUMMARY:` regex capture).
- `validate_output` delegates structural checks to the existing
  `validate_skeleton` helper (reuse, don't duplicate) and additionally
  requires a non-empty `rich_summary`.
- `process_structured_response`: set `skeleton`, `skeleton_display` (via
  `format_skeleton_display`), `rich_reasoning_summary`, and reset
  `extraction_failed` exactly as the successful regex branch does.
- Keep `MAX_EXTRACTION_ATTEMPTS` give-up logic and the moderator-only turn
  order untouched. Rewrite the JSON-block format spec (including the retry
  turn prompt) to name `submit_skeleton`.
- Covering tests: new file + `tests/test_moderator_framing_phases.py`,
  `tests/test_phases_self_distillation.py`.

Commit: `feat(self-distillation): forced submit_skeleton tool for extraction (#23)`

---

### Task 6: ACH `hypothesize` (`submit_hypotheses`)

**Files:** modify `consensus/methods/phases/hypothesize.py`; create
`tests/test_ach_hypothesize_structured.py`.

- Reuse the schema shape of `_belief_helpers.HYPOTHESES_TOOL_PARAMETERS` but
  declare ACH's own spec inline or in an ACH-appropriate location (ACH allows
  accumulating hypotheses across participants — its count bounds differ from
  Belief Diffusion framing; validate each item substantive, at least one).
- `process_structured_response`: dedup new hypotheses against existing ones
  via `word_overlap_similar` exactly as the regex path does, append to
  `state["hypotheses"]`, and display the accepted items as a numbered list.
- Keep `MAX_HYPOTHESIZE_ROUNDS` logic untouched.
- Covering tests: new file + `tests/test_phases_ach.py`,
  `tests/test_moderator_framing_phases.py`.

Commit: `feat(ach): forced submit_hypotheses tool for the hypothesize phase (#23)`

---

### Task 7: Key Assumptions `surface_assumptions` (`submit_assumptions`) + give-up cap

**Files:** modify `consensus/methods/phases/surface_assumptions.py`; create
`tests/test_surface_assumptions_structured.py`.

- `submit_assumptions`: required `assumptions` array of substantive strings.
  Dedup via `word_overlap_similar` and append to `state["assumptions"]` like
  the regex path.
- **Also add the missing give-up cap** (HANDOVER convention: parse-gated
  phases must not loop forever): `MAX_SURFACE_ROUNDS` named constant
  mirroring `hypothesize.py`'s `MAX_HYPOTHESIZE_ROUNDS = 3` —
  `should_advance` returns True once `phase_round > MAX_SURFACE_ROUNDS`
  regardless of parse success. TDD this behavior change explicitly.
- Covering tests: new file + `tests/test_phases_key_assumptions.py`.

Commit: `feat(key-assumptions): forced submit_assumptions tool + give-up cap (#23)`

---

### Task 8: Recursive Decomposition `decompose` (`submit_subquestions`) + give-up cap

**Files:** modify `consensus/methods/phases/decompose.py`; create
`tests/test_decompose_structured.py`.

- `submit_subquestions`: required `sub_questions` array of substantive
  strings. Dedup via `word_overlap_similar`, append to
  `state["sub_questions"]` like the regex path.
- **Add the missing give-up cap**: `MAX_DECOMPOSE_ROUNDS = 3` mirroring
  Task 7, with the same TDD treatment.
- Covering tests: new file + `tests/test_recursive_decomposition.py`.

Commit: `feat(recursive-decomposition): forced submit_subquestions tool + give-up cap (#23)`

---

### Task 9: Counterfactual `counterfactual_extract` (`submit_claims`, moderator turn)

**Files:** modify `consensus/methods/phases/counterfactual_extract.py`;
create `tests/test_counterfactual_extract_structured.py`.

- `submit_claims`: required `claims` array of substantive strings and a
  required `preliminary_conclusion` string (replaces the `CONCLUSION:` regex
  capture).
- `process_structured_response`: build the same `claims`
  (`[{"id", "text"}]`) and parallel `claim_results` structures the regex
  path builds, set `preliminary_conclusion` (respecting the existing
  `prior_conclusion` precedence), and reset `extraction_failed` as the
  successful branch does.
- Keep `MAX_EXTRACTION_ATTEMPTS` give-up logic and moderator-only turn order
  untouched.
- Covering tests: new file + `tests/test_phases_counterfactual.py`,
  `tests/test_moderator_framing_phases.py`.

Commit: `feat(counterfactual): forced submit_claims tool for extraction (#23)`

---

### Task 10: tool-capability check in `switch_discussion_method`

**Files:** modify `consensus/app_discussion_flow.py`; extend
`tests/test_app_discussion_flow.py` (or a new focused test file).

- Triage's runtime method switch currently bypasses the setup gate: it can
  switch a live discussion into a structured method whose panel models are
  known to lack tool support.
- Reuse `_validate_structured_output_support` from
  `consensus/app_discussion_setup.py` (import it — do not duplicate; move it
  to a shared location only if importing creates a cycle) inside
  `switch_discussion_method`, before any state mutation. On failure return
  `{"error": <message>}` without mutating `method_state` or posting the
  transition message — the triage flow then falls through to
  `method_complete` per HANDOVER.
- Tests: switching into a structured method with a known non-tool-capable
  model returns the error and leaves the discussion's method/state untouched;
  unknown capability and unstructured targets still switch fine.

Commit: `fix(triage): run tool-capability check on runtime method switch (#23)`

---

### Task 11: recommender awareness + docs/handover

**Files:** modify `consensus/methods/recommender.py` and/or its caller
`recommend_method` in `consensus/app_discussion_setup.py`; modify
`HANDOVER.md`, `CLAUDE.md` if needed; extend `tests/test_recommender.py`.

- `MethodRecommender` does not know panel models. Implement the minimal
  honest version: `recommend_method` (the caller, which has `db` access)
  accepts an optional list of panel model identifiers; when provided, any
  recommended method whose `DiscussionMethod.requires_structured_output()`
  is True and where `db.pricing.supports_tools()` is `False` for any panel
  model gets down-ranked (moved after capability-compatible
  recommendations, confidence annotated in `reasoning`) — never silently
  dropped. When panel info is absent, behavior is unchanged. Wire the
  parameter through the existing call sites where panel data is available;
  if no call site has panel data yet, expose the parameter, cover it with
  tests, and record the frontend wiring as a follow-up in HANDOVER.
- Update `HANDOVER.md`: move the finished conversions into "Where things
  stand" (note `tally` needed no conversion and why), remove the closed
  gaps, keep the #24/#25/#27/#26 ordering, and note the give-up caps added
  to `surface_assumptions` / `decompose`.
- Run the full suite one final time.

Commit: `feat(recommender): down-rank structured methods for non-tool-capable panels; docs (#23)`
