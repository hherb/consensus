# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-14 (session: #27 Double Crux, branch
`claude/handover-instructions-86694a`)._

This file briefs the next session(s) on what was done, what is in flight,
and what to do next. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive.

## Where things stand

- **PR #18** (six defect classes, issues #12–#17), **PR #31**
  (#19/#20/#21), **PR #35** (#22 phase-machine loop support), **PR #36**
  (#30 Belief Diffusion abort), **PR #38** (#23 mechanism + first
  three conversions), **PR #39** (#23 remaining conversions +
  hardening), **PR #40** (#24 NGT), and **PR #41** (#25 MCDA) are all
  **merged**.
- **#27 Double Crux implemented** (this session, **PR #43** — merge it
  before building on this work).  Plan:
  `docs/superpowers/plans/2026-07-14-double-crux.md`.
  Method `double_crux` (`consensus/methods/double_crux.py`), five
  phases: the reused `StatePositionsHandler` (now parametrized with
  `context_label` — default preserves the Adversarial Collaboration
  wording) plus four new handlers in `consensus/methods/phases/`
  (`hunt_cruxes`, `identify_crux`, `test_crux`, `resolve_crux`) over a
  shared `_crux_helpers.py`:
  - Three structured phases per the #23 pattern: `submit_cruxes`
    (participants name the claims that would change their mind, each
    with a 0–1 belief probability), `submit_crux_selection`
    (moderator-only verdict: `factual` / `values` / `none`), and
    `submit_resolution` (final stance + position + `crux_belief`,
    required iff a factual crux was tested).  Positions and crux
    testing are free-text phases.
  - **Verdict routing uses the #22 `next_phase` mechanism:** `factual`
    → linear to `test_crux`; `values` → jump to `resolve` (nothing
    factual to test); `none` → loop back to `hunt_cruxes` (verdict and
    attempt counter reset, `crux_search_rounds` incremented) until
    `MAX_CRUX_SEARCH_ROUNDS`, then finalised as `none` and `resolve`
    still runs — the method reports a clean disagreement map instead
    of a resolution.  Worst-case transitions stay well under the loop
    guard.
  - **Belief shift is the success metric** (per the issue): initial
    beliefs are snapshotted from the selected cruxes at identification
    time, final beliefs restated at resolution, and shifts computed
    deterministically (never by the model) into
    `method_state["crux_map"]` — the machine-readable outcome artifact
    (verdict, shared crux, positions, cruxes, resolutions,
    belief_shifts, caveats), mirroring MCDA's `decision_artifact`.
  - Aborts/give-ups: zero cruxes after `MAX_HUNT_ROUNDS` aborts the
    method (`generate_ideas.py` pattern); `MAX_IDENTIFY_ATTEMPTS`
    gates the moderator fallback path; `MAX_RESOLVE_ROUNDS` gates
    resolution.  Same-entity cruxes are word-overlap deduped but
    cross-entity near-duplicates are kept — overlap between parties is
    exactly the shared-crux signal.
  - Recommender `_TAXONOMY` gained: "Resolving disagreements by
    finding the pivotal factual claim beneath them → Double Crux".
  - Tests: `test_crux_helpers.py`, `test_phases_double_crux.py`,
    `test_double_crux_structured.py` (139 tests; suite total 2120).
- **#25 Weighted Decision Matrix implemented** (PR #41, merged).  Plan:
  `docs/superpowers/plans/2026-07-14-weighted-decision-matrix.md`.
  Method `decision_matrix` (`consensus/methods/decision_matrix.py`),
  five phases from new handlers in `consensus/methods/phases/`
  (`enumerate_options`, `weight_criteria`, `score_options`,
  `analyse_sensitivity`, `decide`) over two shared helper modules —
  `_mcda_helpers.py` (recording/validation) and `_mcda_analysis.py`
  (aggregation/sensitivity/artifact/formatting; split to respect the
  ~500-line file rule):
  - Generalises the two handlers named in issue #25: `define_criteria`
    → weighted criteria (weight votes averaged per participant, merge
    by name similarity, resubmission replaces own vote), and
    `evaluate_matrix` → option×criterion scoring (O/C labels, two-level
    `additionalProperties` schema, partial coverage defaults missing
    cells to the midpoint `DEFAULT_SCORE`).
  - Four structured phases per the #23 pattern: `submit_options`,
    `submit_weighted_criteria`, `submit_scores`, `submit_decision`
    (its required `rationale` plays the `reasoning` role, like
    `submit_claims`).  Sensitivity is a moderator-only presentational
    phase — all numbers (weighted totals, divergence, one-at-a-time
    sensitivity) are computed deterministically in `_mcda_analysis`,
    never by the model.
  - **The decision artifact** (`method_state["decision_artifact"]`) is
    the issue's machine-readable output: ranked options with weighted
    totals + per-criterion means, effective weights, per-participant
    divergence, sensitivity report, recommendation, rationale, caveats.
    Both the structured and free-text decide paths record it (the
    fallback defaults the recommendation to the top-ranked option with
    an explanatory caveat).
  - Aborts: zero options after `MAX_OPTIONS_ROUNDS` or zero criteria
    after `MAX_CRITERIA_ROUNDS` end the method early
    (`generate_ideas.py` pattern); score/decide carry defensive
    degenerate guards (`get_output_tool -> None`).
  - Recommender: `_TAXONOMY` gained an MCDA line ("Decision-making by
    scoring options against weighted criteria").
  - Review follow-ups (same PR): `record_scores` drops unknown O/C
    labels, so a mislabelled free-text matrix no longer counts its
    author as a scorer with every cell defaulted (which inflated
    divergence); the decision artifact gains an explicit caveat when
    zero participants scored (the ranking is contentless);
    `record_criteria` no longer reports a criterion twice when one
    submission merges two similar names into it; score tables round
    floats to the artifact's 2-dp precision.  Order-dependent
    word-overlap merging (first-name-wins) is catalog-wide, not
    MCDA-specific — tracked as **issue #42**.
- **#24 Nominal Group Technique** (2026-07-14 session, **PR #40**,
  merged).  Plan:
  `docs/superpowers/plans/2026-07-14-nominal-group-technique.md`.
  Method `nominal_group` (`consensus/methods/nominal_group.py`), five
  phases assembled from new handlers in `consensus/methods/phases/`
  (`generate_ideas`, `cluster_ideas`, `clarify_ideas`,
  `allocate_points`, `rank_ideas`) over a shared `_ngt_helpers.py`:
  - Three structured phases per the #23 pattern: `submit_ideas`
    (participants, anonymised silent generation), `submit_candidates`
    (moderator-only clustering), `submit_points` (fixed pool of
    `POINTS_PER_VOTER` points, validator enforces exact sum).
    Clarify and rank are free-text phases.  The point-pool rules bind
    on the free-text path too (`check_free_text_allocations`): batches
    are all-or-nothing and an entity that has allocated cannot top up
    on a later turn — review finding on PR #40.
  - Give-up caps: `MAX_GENERATE_ROUNDS`, `MAX_CLUSTER_ATTEMPTS`,
    `MAX_ALLOCATE_ROUNDS`.  Generation with zero ideas aborts the
    method (frame_hypotheses pattern); clustering give-up instead
    *promotes raw deduplicated ideas to candidates 1:1* and continues.
  - Open Discussion is now recommendable: `_EXCLUDED_METHODS` is
    `{"triage"}` and `_TAXONOMY` gained an NGT line (owner decision
    2026-07-12, executed with #24).
- **#23 structured outputs — ALL remaining regex-parsing phases
  converted** (2026-07-13 session, **PR #39**, merged). Plan:
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
   - **#26 Tree-of-Thoughts** — generate/score/prune/expand; the #22
     `next_phase` hook it needed now exists.

2. **Cross-cutting quality:**
   - **#28 evidence-gated phases** — opt-in `require_citations` so
     evidence phases must ground claims via the existing RAG/web tools.
     The Double Crux `test_crux` phase is the flagship consumer (its
     prompt already directs participants at research/document tools).
   - **#29 same-model-panel warning** for Delphi/Belief Diffusion.

3. **New known follow-ups (#27 session, 2026-07-14):**
   - **Double Crux belief shift is only measured for crux authors.**
     `initial_beliefs` is snapshotted from the cruxes the moderator
     selects, so a participant whose own crux wasn't selected has no
     "initial" end (map shows `? → final`, no shift).  An optional
     pre-testing belief poll on the shared crux (one structured
     micro-turn after identification) would complete the metric —
     evaluate whether the extra turn is worth it.
   - **The identify loop re-runs positions' context, not the phase.**
     Loop-backs re-enter `hunt_cruxes` only; if hunting keeps failing
     because positions were vague, there is no path back to
     `positions`.  Acceptable for now (the hunt prompt asks for
     convergence), noting it in case real transcripts show otherwise.

4. **Known follow-ups (#25 session, 2026-07-14):**
   - **MCDA free-text weights only parse the `(weight: N)` suffix.**
     `extract_weighted_criteria` recognises `1. Name (weight: 4)` /
     `[weight = 4]`; a human writing weights in prose gets
     `DEFAULT_WEIGHT` silently.  Fine for the AI path (structured tool
     enforces weights); a UI hint for human participants would close
     the gap.
   - **No real-pipeline (`complete_turn`) flow test for the
     NGT/MCDA/Double Crux methods yet** — handler-level and
     structured-conversion coverage matches the NGT precedent, but
     none of the three has a `tests/test_turn_order_flow.py`-style
     end-to-end test driving the moderator flow.  Worth adding once,
     covering all three (Double Crux would also exercise the identify
     loop through the real `advance_phase` path — only
     `test_phase_machine_loops.py` covers loops end-to-end today).

5. **Known follow-ups (older sessions):**
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
     (The cosmetic gap originally noted here is closed: down-ranked
     recommendations now carry a `capability_warning` field rendered
     as a ⚠ badge by `renderRecommendations()`, so the untouched
     confidence score no longer visually contradicts the ordering.)
   - **PR #39 post-review hardening (2026-07-14) is on the PR branch:**
     `validate_skeleton` rejects non-string ids/text (a truthy int
     previously crashed `format_skeleton_display` and the crash was
     swallowed as a misleading "API error" without advancing the
     extraction give-up counter); the `MAX_SURFACE_ROUNDS` /
     `MAX_DECOMPOSE_ROUNDS` give-ups now log a warning and the
     downstream zero-item transition/turn prompts explain the empty
     list instead of announcing "0 assumptions"; structured items are
     `rstrip('.')`-normalised to match the regex paths (hypothesize,
     define_criteria, counterfactual claims); belief payloads must sum
     to ~1 (`BELIEF_SUM_TOLERANCE`); the blocked-switch transcript
     notice dedups per target method rather than globally; the belief
     value schema carries `minimum`/`maximum` and the claims schema
     `minItems`/`maxItems` (accumulative schemas stay deliberately
     unbounded); `evaluate_matrix` prompts no longer name the tool
     when the degenerate empty matrix means none is offered; and the
     counterfactual extract display shows the conclusion actually kept,
     not a discarded payload one. The shared `str(None)` validator
     helper below remains open.

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

- **#23: it is acceptable to require tool-capable models for methods
  with structured phases.** The regex fallback does not need to remain
  first-class — the implemented design forces tool calls and surfaces a
  clear setup-time error (not a silent degrade) when a participant's
  model/provider is known to lack tool support.
