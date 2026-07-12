# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-12 (session: discussion-methods review, branch
`claude/consensus-discussion-review-f5af59`, PR #18)._

This file briefs the next session(s) on what was done, what is in flight,
and what to do next. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive.

## What happened in this session

A systematic three-pass review of the discussion-methods subsystem
(`consensus/methods/`, its 43 phase handlers, and the moderator/flow
integration) found six classes of real defects. All were fixed with
test-driven regression coverage and shipped as **PR #18**, which closes
issues **#12–#17** on merge. Highlights of the fixes (details in the PR
body and the issues):

- **#13 turn-order cascade** — `get_turn_order` hooks now receive the
  full roster (`Discussion.base_turn_order`) via
  `apply_method_turn_order()` / `method_roster()` in
  `app_discussion_flow.py`, never the previous phase's narrowed order.
- **#12 court huddle privacy** — huddle suppression survives the
  `[Name]: ` prefix; suppressed messages are dropped entirely; huddles
  run 2 rounds.
- **#14 method_complete** — frontend concludes the discussion when a
  method exhausts its phases (`handleTurnLimitFlags` in
  `consensus/static/discussion-actions.js`).
- **#15 framing/capture dead code** — belief-diffusion/premortem framing
  are moderator-only phases; counterfactual conclusion and
  self-distillation rich summary are captured in the extract/distill
  moderator turns; loop caps added to condition-based phases.
- **#16 persistence** — method_state persisted every completed turn;
  phase turn order recorded in `method_state["_turn_order"]` and
  restored by `load_discussion`; `turn_number` restored as max+1;
  **human messages now run `method.process_response`** (human votes and
  estimates used to be ignored); method switches keep budget keys.
- **#17 assorted** — Delphi label consistency + word-boundary
  anonymisation + zero-median convergence; triage word-boundary method
  matching; no-motion vote skip; ACH inline-ratings brace matcher;
  sub-question attribution by header number.

Test count went from 1287 to **1362 passing** (75 new tests in
`tests/test_turn_order_flow.py`, `tests/test_court_huddle_privacy.py`,
`tests/test_moderator_framing_phases.py`,
`tests/test_method_state_persistence.py`,
`tests/test_small_method_defects.py`).

## Next steps, in order

1. **Merge PR #18** (human review + merge). Everything below assumes it
   lands; several open issues reference code it introduces.

2. **Remaining bugs (small, well-scoped):**
   - **#19** — mid-round condition-based phase transitions start the new
     phase at `current_turn_index > 0`, truncating its first round.
     Suggested fix: reset the index unconditionally on any phase
     transition in `complete_turn`.
   - **#20** — Red Team rotation is described but unimplemented
     (`red_team_rotation` state is dead). Either implement rotation
     (blocked on #22) or fix the description.
   - **#21** — remove (or actually wire up) `ProcessedResponse.extracted_data`;
     its only caller discards it.

3. **Architectural enablers (do before new methods):**
   - **#22 phase-machine loop support** — let a method choose the next
     phase (`next_phase()` hook with linear default + loop guard).
     Unlocks #20, #26, and true recursion in recursive_decomposition.
   - **#23 function-calling for structured outputs** — phases declare an
     output tool (`submit_estimate`, `submit_ratings`, ...) enforced via
     `ai_client.complete_with_tools()`; regex parsing stays as fallback.
     Removes the whole class of "silently unparseable response" bugs.

4. **New methods (highest value first):**
   - **#24 Nominal Group Technique** — structured brainstorming; ~80% of
     phases exist as reusable handlers (Delphi anonymisation, list
     parsing/dedup, voting/tally). The catalog currently has no
     generative method — this is the biggest functional gap.
   - **#25 Weighted decision matrix (MCDA)** — generalise
     `define_criteria` + `evaluate_matrix` into a standalone decision
     method with a structured, machine-readable final artifact.
   - **#27 Double Crux** — disagreement resolution by crux-finding;
     pairs with belief tracking.
   - **#26 Tree-of-Thoughts** — generate/score/prune/expand; needs #22.

5. **Cross-cutting quality:**
   - **#28 evidence-gated phases** — opt-in `require_citations` so
     evidence phases must ground claims via the existing RAG/web tools.
   - **#29 same-model-panel warning** for Delphi/Belief Diffusion.

## Conventions and gotchas for the next session

- **Never derive a phase turn order from the incoming `entity_ids` by
  filtering the current order.** Handlers receive the full roster; if
  you need "everyone except X", filter the roster. The flow guards
  against empty orders, but don't rely on it.
- **`method_state` keys starting with `_` are internal bookkeeping**
  (`_turn_order`, `_panelist_map`, `_continuation_count`,
  `_original_max_rounds`, `_original_cost_limit`). `switch_discussion_method`
  preserves the budget keys explicitly — if you add new bookkeeping that
  must survive a method switch, add it to the preserved set in
  `app_discussion_flow.switch_discussion_method`.
- **Moderator summaries never pass through `process_response`.** If a
  method must capture something from the moderator, give that phase
  `get_turn_order -> [moderator_id]` so the moderator takes a real turn
  (see `counterfactual_extract.py`, `distill_skeleton.py`,
  `frame_hypotheses.py` for the pattern, including bounded retries).
- **Every condition-based phase (`rounds=0`) needs a give-up cap** so an
  unparseable group cannot loop forever (`MAX_*_ATTEMPTS` /
  `MAX_*_ROUNDS` constants — no magic numbers, per
  `docs/llm/golden_rules.md`).
- **Test new flow behavior through the real pipeline.** The historical
  failure mode here was unit tests feeding handlers idealized inputs the
  moderator never produces. Use the patterns in
  `tests/test_turn_order_flow.py` / `tests/test_method_state_persistence.py`:
  drive `complete_turn` with a human moderator plus `moderator_summary`
  (no network needed), and `Moderator._format_messages` for context
  filtering.
- Project rules: `uv` only (never pip), TDD (failing test first), files
  under ~500 lines, docstrings + type hints mandatory.

## Decisions from the repo owner (2026-07-12)

- **Open Discussion becomes recommendable once #24 (NGT) exists.** When
  implementing #24, also remove `"open_discussion"` from
  `_EXCLUDED_METHODS` in `consensus/methods/recommender.py` and update
  the `_TAXONOMY` line that marks it "(fallback only)".
- **#23: it is acceptable to require tool-capable models for methods
  with structured phases.** The regex fallback does not need to remain
  first-class — design the phase output contract around forced tool
  calls, and surface a clear setup-time error (not a silent degrade)
  when a participant's model/provider lacks tool support.
