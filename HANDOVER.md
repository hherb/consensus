# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-13 (session: phase-machine loop support, branch
`claude/handover-instructions-7598ab`)._

This file briefs the next session(s) on what was done, what is in flight,
and what to do next. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive.

## Where things stand

- **PR #18** (six defect classes from the discussion-methods review,
  issues #12–#17) is **merged**.
- **#19, #20, #21** were fixed in a prior session (merged via PR #31):
  rotation resets on every phase transition (and survives reload), the
  Red Team description matches its single-pass behavior, and
  `ProcessedResponse.extracted_data` was removed.
- **#22 phase-machine loop support** was implemented in this session
  (**PR #35**): `DiscussionMethod.next_phase(discussion) -> str | None`
  chooses the next phase by name; `PhaseHandler.next_phase` can return a
  phase name (jump/loop), `None` (abort the method early), or the
  `LINEAR_NEXT` sentinel (default linear order). `advance_phase` in
  `consensus/methods/base.py` enforces a loop guard:
  `max_phase_entries` per method, defaulting to
  `len(default_phases) * MAX_PHASE_VISITS_PER_PHASE`. Transition count
  lives in `method_state["_phase_entries"]`. 16 new tests in
  `tests/test_phase_machine_loops.py`, including a full
  diverge→converge→diverge cycle through the real `complete_turn`
  pipeline. Existing linear methods are behaviorally unchanged.

## Next steps, in order

1. **Merge PR #35 (#22 loop support).** Everything below can build on
   the `next_phase` hook once it lands.

2. **#30 Belief Diffusion method-abort** — now unblocked by #22: give
   the framing phase's handler a `next_phase` override that returns
   `None` when `MAX_FRAMING_ATTEMPTS` is exhausted and no hypotheses
   were parsed, so the method ends (`method_complete`) instead of
   running prior/diffuse/diagnose against an empty hypothesis list.
   Acceptance criterion is on the issue.

3. **#23 function-calling for structured outputs** — phases declare an
   output tool (`submit_estimate`, `submit_ratings`, ...) enforced via
   `ai_client.complete_with_tools()`. Per the owner decision below,
   tool-capable models may be required; surface a setup-time error
   rather than silently degrading.

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
   - **#26 Tree-of-Thoughts** — generate/score/prune/expand; the #22
     `next_phase` hook it needed now exists.

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
