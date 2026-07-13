# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-13 (session: #23 remaining conversions, branch
`claude/handover-instructions-4be219`)._

This file briefs the next session(s) on what was done, what is in flight,
and what to do next. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive.

## Where things stand

- **PR #18** (six defect classes, issues #12–#17), **PR #31**
  (#19/#20/#21), **PR #35** (#22 phase-machine loop support), **PR #36**
  (#30 Belief Diffusion abort), and **PR #38** (#23 mechanism + first
  three conversions) are all **merged**.
- **#23 structured outputs — ALL remaining regex-parsing phases
  converted** (this session, **PR #39** — merge it before building on
  this work). Plan:
  `docs/superpowers/plans/2026-07-13-structured-conversions-remaining.md`
  (all 11 tasks executed). See
  `docs/superpowers/plans/2026-07-13-structured-method-outputs.md` for
  the original mechanism design.
  - Newly converted phases, each following the established pattern
    (schema + `validate_output` + `process_structured_response`, prompts
    rewritten to name the tool, regex `process_response` kept as the
    human/fallback path):
    - Belief Diffusion `prior_beliefs` / `diffuse_beliefs` →
      `submit_beliefs` (H-label keyed belief distributions — a JSON
      object mapping each hypothesis label to a probability).
    - Self-Distillation `blind_evaluate` → `submit_validity_scores`.
    - Adversarial Collab `define_criteria` → `submit_criteria`.
    - ACH `evaluate_matrix` → `submit_matrix_ratings`.
    - Self-Distillation `distill_skeleton` → `submit_skeleton`.
    - ACH `hypothesize` → `submit_hypotheses` (ACH-specific spec —
      distinct from Belief Diffusion's `frame`'s tool of the same name).
    - Key Assumptions `surface_assumptions` → `submit_assumptions`.
    - Recursive Decomposition `decompose` → `submit_subquestions`.
    - Counterfactual `counterfactual_extract` → `submit_claims`.
  - `tally.py` needed **no conversion**: `TallyHandler` has no
    `process_response` override (the base `PhaseHandler` no-op is used
    as-is) — the vote tally itself is computed by `_voting_helpers` at
    conclusion, not parsed from a turn.
  - Give-up caps added alongside their conversions:
    `MAX_SURFACE_ROUNDS` (`surface_assumptions.py`) and
    `MAX_DECOMPOSE_ROUNDS` (`decompose.py`), per the existing
    condition-based-phase convention.
  - **Both #23 gaps from the previous session are now closed:**
    - `switch_discussion_method` (`app_discussion_flow.py`) now runs
      `_validate_structured_output_support` before committing a Triage
      handoff. The check itself moved to `consensus/structured_output.py`
      (re-exported from `app_discussion_setup` for backward
      compatibility) because `app_discussion_setup` and
      `app_discussion_flow` import each other, and both needed the
      helper — a same-module home would have created a circular import.
      A blocked switch returns an error, is logged, posted to the
      transcript as a system message, and surfaced to the frontend via a
      `switch_error` field (toasted) instead of silently falling through.
    - `MethodRecommender` is now capability-aware:
      `consensus.methods.recommender.downrank_incompatible_recommendations()`
      takes the ranked recommendations, a `db` handle, and a list of
      `(model, base_url)` panel pairs; any recommendation whose method
      `requires_structured_output()` is moved after all
      capability-compatible recommendations when
      `db.pricing.supports_tools()` is `False` for any panel model, with
      a note appended to `reasoning` (never dropped). Unknown capability
      (`None`) never down-ranks. `app_discussion_setup.recommend_method`
      (the db-aware caller) accepts optional `db` and `panel_models`
      parameters and calls the helper only when both are supplied;
      omitting either leaves behavior unchanged. `ConsensusApp.recommend_method`
      (`app.py`) is wired: the New Discussion tab already lists
      participants (with their models) above the "Suggest Method" button
      (`consensus/static/index.html` — roster at line ~123, button at
      line ~174), so panel data is available at this call site and is
      passed through as `(model, base_url)` pairs from
      `self.discussion.entities`.

## Next steps, in order

1. **New methods (highest value first):**
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

2. **Cross-cutting quality:**
   - **#28 evidence-gated phases** — opt-in `require_citations` so
     evidence phases must ground claims via the existing RAG/web tools.
   - **#29 same-model-panel warning** for Delphi/Belief Diffusion.

3. **New known follow-ups (this session):**
   - **Blocked Triage switch still auto-concludes the discussion.** When
     `switch_discussion_method` rejects a handoff (non-tool-capable
     model), Triage currently falls through to `method_complete` and the
     discussion ends. A "reassign model and retry" UX — pause, let the
     user swap the offending participant's model, then retry the
     switch — would be considerably better than ending the discussion
     outright. No mechanism for this exists yet.
   - **Structured payload validators share a fragile string-coercion
     pattern.** `str(payload.get(x, "")).strip()` (23 call sites across
     `consensus/methods/phases/*.py`) silently accepts a JSON literal
     `null` for `x` as the Python string `"None"`, because
     `payload.get("x", "")` only substitutes the default when the key is
     *absent*, not when it is present with value `null`. A model that
     omits a field gets the safe `""` default; a model that includes the
     field as `null` gets the misleading string `"None"`. One shared
     helper (e.g. `_coerce_str(payload, key)` treating both `None` and
     absence as `""`) applied across all structured phases would harden
     this in one place rather than 23.
   - **Recommender panel-model wiring needed no frontend change.** The
     New Discussion tab already builds the roster (with each entity's
     model) before "Suggest Method" is clickable, and
     `ConsensusApp.recommend_method` reads `self.discussion.entities`
     server-side — so `consensus/static/setup.js`'s existing
     `api.recommendMethod(topic, answerType)` call needed no changes.
     One purely cosmetic gap remains: `renderRecommendations()` in
     `setup.js` shows `reasoning` as plain text, so the appended "Note:
     requires tool-capable models..." sentence is legible but not
     visually distinguished (e.g. a warning icon or muted badge) from
     the rest of the reasoning. Not functional — the down-ranked
     position and the note text already convey the signal.

## Conventions and gotchas for the next session

- **Structured-phase conversions must keep `process_response`.** Humans
  type free text, and the structured path falls back to it after
  exhausted retries — not dead code. Note the fallback rarely *extracts*
  anything (the rewritten prompts no longer describe the JSON-block
  format); the real extraction containment is each phase's give-up cap
  (`MAX_FRAMING_ATTEMPTS`, `MAX_VOTE_ROUNDS`, `phase_round` advancement).
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
- **Structured conversions include a required `reasoning` field**,
  rendered before the data display (belief bar, matrix, skeleton, ...) so
  a validated payload still reads as a real contribution rather than a
  bare data dump. Two exceptions: `submit_beliefs` declares `reasoning`
  but leaves it optional (unvalidated — a belief turn may render as a
  bare belief bar), and `submit_claims` has no `reasoning` field at all
  (its `preliminary_conclusion` — like `submit_skeleton`'s
  `rich_summary` — plays that role for the moderator extraction phases). Dynamic-key maps (belief distributions keyed by
  hypothesis label, matrix ratings keyed by hypothesis × evidence label)
  declare `additionalProperties` in their JSON Schema rather than
  enumerating keys, since the key set is only known at runtime (see
  `MATRIX_TOOL_PARAMETERS` in `evaluate_matrix.py` and the shared
  `BELIEFS_TOOL_PARAMETERS` pattern in `_belief_helpers.py`).
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
