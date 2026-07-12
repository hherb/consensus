# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-13 (session: #23 structured outputs via native
function calling, branch `claude/handover-instructions-e198e6`)._

This file briefs the next session(s) on what was done, what is in flight,
and what to do next. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive.

## Where things stand

- **PR #18** (six defect classes, issues #12–#17), **PR #31**
  (#19/#20/#21), **PR #35** (#22 phase-machine loop support), and
  **PR #36** (#30 Belief Diffusion abort) are all **merged**.
- **#23 structured outputs — mechanism + first three conversions done**
  (this session). Plan:
  `docs/superpowers/plans/2026-07-13-structured-method-outputs.md`.
  - `consensus/structured_output.py` — `generate_structured_turn()`
    forces the phase's declared tool via `tool_choice`, validates the
    payload with the handler's `validate_output` hook, retries with the
    validation error fed back (`MAX_STRUCTURED_OUTPUT_ATTEMPTS = 3`),
    then falls back to the free-text path with a user-visible warning.
    HTTP 400 (model without tool support) raises a loud
    `StructuredOutputError`.
  - Hooks: `OutputToolSpec` in `consensus/methods/base.py`;
    `PhaseHandler.get_output_tool` / `validate_output` /
    `process_structured_response` + `requires_structured_output`
    ClassVar; `DiscussionMethod` delegates all of them and exposes
    `requires_structured_output()`.
  - Flow: `Moderator.generate_turn` routes structured phases (registry
    tools are NOT offered on those turns); `generate_ai_turn` sends
    `AIResponse.structured_output` to `process_structured_response`,
    everything else (incl. human input) through the regex
    `process_response` path, which remains as containment fallback.
  - Setup gate: migration 014 adds `supported_parameters` to
    `model_pricing`; `PricingCache.supports_tools()` returns
    True/False/None (None = unknown → allowed, fails loudly at runtime);
    `start_discussion` rejects structured methods when any AI member's
    model is known to lack tool support
    (`_validate_structured_output_support` in `app_discussion_setup.py`).
  - Converted phases: Delphi `estimate` + `revise` (`submit_estimate`),
    Voting `vote` (`submit_votes`), Belief Diffusion `frame`
    (`submit_hypotheses` — closes the #30 failure mode at the source;
    the abort machinery stays as last-resort containment).

## Next steps, in order

1. **Finish the #23 conversions** — mechanical, following the pattern in
   the converted handlers (schema + `validate_output` +
   `process_structured_response`, shared helpers refactored for reuse,
   prompts rewritten to name the tool, existing regex path kept for
   humans/fallback). Remaining regex-parsing phases:
   `prior_beliefs` / `diffuse_beliefs` (belief distributions),
   `blind_evaluate` / `tally`, `evaluate_matrix` / `define_criteria`,
   `distill_skeleton`, `hypothesize`, `surface_assumptions`,
   `decompose`, `counterfactual_extract`.

2. **Known #23 gaps (small, self-contained):**
   - `switch_discussion_method` (triage handoff) does not run the
     tool-capability check, so Triage can still switch into a structured
     method with a non-tool-capable model. Add the check there (return
     an error → triage falls through to `method_complete`).
   - `MethodRecommender` does not consider tool capability when
     recommending methods; it could down-rank structured methods when
     panel models lack tool support.

3. **New methods (highest value first):**
   - **#24 Nominal Group Technique** — structured brainstorming; ~80% of
     phases exist as reusable handlers (Delphi anonymisation, list
     parsing/dedup, voting/tally). The catalog currently has no
     generative method — this is the biggest functional gap.
   - **#25 Weighted decision matrix (MCDA)** — generalise
     `define_criteria` + `evaluate_matrix` into a standalone decision
     method with a structured, machine-readable final artifact.
   - **#27 Double Crux** — disagreement resolution by crux-finding;
     pairs with belief tracking.
   - **#26 Tree-of-Thoughts** — generate/score/prune/expand; the #22
     `next_phase` hook it needed now exists.

4. **Cross-cutting quality:**
   - **#28 evidence-gated phases** — opt-in `require_citations` so
     evidence phases must ground claims via the existing RAG/web tools.
   - **#29 same-model-panel warning** for Delphi/Belief Diffusion.

## Conventions and gotchas for the next session

- **Structured-phase conversions must keep `process_response`.** Humans
  type free text, and the structured path falls back to it after
  exhausted retries. The regex path is the containment layer, not dead
  code.
- **Never derive a phase turn order from the incoming `entity_ids` by
  filtering the current order.** Handlers receive the full roster; if
  you need "everyone except X", filter the roster. The flow guards
  against empty orders, but don't rely on it.
- **`method_state` keys starting with `_` are internal bookkeeping**
  (`_turn_order`, `_panelist_map`, `_continuation_count`,
  `_original_max_rounds`, `_original_cost_limit`, `_phase_entries` —
  the loop-guard transition counter). `switch_discussion_method`
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
  filtering. For structured turns, stub `complete_with_tools` (see
  `tests/test_structured_output.py`).
- Project rules: `uv` only (never pip), TDD (failing test first), files
  under ~500 lines, docstrings + type hints mandatory.

## Decisions from the repo owner (2026-07-12)

- **Open Discussion becomes recommendable once #24 (NGT) exists.** When
  implementing #24, also remove `"open_discussion"` from
  `_EXCLUDED_METHODS` in `consensus/methods/recommender.py` and update
  the `_TAXONOMY` line that marks it "(fallback only)".
- **#23: it is acceptable to require tool-capable models for methods
  with structured phases.** The regex fallback does not need to remain
  first-class — the implemented design forces tool calls and surfaces a
  clear setup-time error (not a silent degrade) when a participant's
  model/provider is known to lack tool support.
