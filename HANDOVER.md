# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-16 (`coerce_str` payload-coercion hardening merged via
PR #50 — no issue, tech-debt from this file. All tracked issues closed. Main
at 2372 tests; this branch adds 4 real-pipeline method-flow E2E tests → 2376.)._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `docs/superpowers/specs/`, and
`docs/superpowers/plans/` — do not re-narrate it here.

## What is done (all merged)

| Work | Issues | PR |
|------|--------|----|
| Six defect classes | #12–#17 | #18 |
| Method fixes | #19/#20/#21 | #31 |
| Phase-machine loop support (`next_phase`) | #22 | #35 |
| Belief Diffusion abort | #30 | #36 |
| Structured outputs — mechanism + first conversions | #23 | #38 |
| Structured outputs — all remaining regex phases converted + hardening | #23 | #39 |
| Nominal Group Technique (`nominal_group`) | #24 | #40 |
| Weighted Decision Matrix / MCDA (`decision_matrix`) | #25 | #41 |
| Double Crux (`double_crux`) | #27 | #43 |
| Tree of Thoughts (`tree_of_thoughts`) | #26 | #44 |
| Evidence-tracked phases (soft grounding) | #28 | #45 |
| Order-independent contribution merging | #42 | #46 |
| Same-model panel warning (Delphi/Belief) | #29 | #47 |
| Participating moderator counted as estimator | #48 | #49 |
| `coerce_str` payload-coercion hardening | — (tech debt) | #50 |

Main is at **2372 tests passing** (2376 on this branch, which adds 4
real-pipeline method-flow E2E tests — not yet merged). Every tracked issue is
merged and closed; there are no open issues or PRs.

**#28 evidence-tracked phases** merged (PR #45) —
spec `docs/superpowers/specs/2026-07-14-evidence-gated-phases-design.md`, plan
`docs/superpowers/plans/2026-07-14-evidence-tracked-phases.md`. New module
`consensus/evidence.py` (turn-level grounding classifier — tool-call + inline
paths, `record_and_annotate_evidence`, `build_evidence_summary`); opt-in
`Phase.track_evidence` flag; flow wiring in `app_discussion_flow.py` (AI +
human turns, gated on the active phase); `test_crux` is the sole opted-in
phase, with the summary surfaced in Double Crux's `crux_map` artifact and
factual conclusion prompt; minimal "Attach evidence" UI button inserting the
`[evidence: …]` marker. **Soft by design** (owner decision): ungrounded turns
are annotated + logged, never blocked — see
`memory/evidence-gating-philosophy.md`. Suite after #28: **2295 passing**.

Deferred #28 follow-ups (not built this slice): per-claim citation mapping;
opting in Adversarial Collab `gather_evidence` and ACH `present_evidence`
(prove on `test_crux` first); a hard-retry enforcement variant (deliberately
rejected for now); a richer source-picker UI beyond the marker inserter;
knowledge-graph grounding (no KG participant tool exists yet); and the
**live browser click-through of the Attach-evidence button** (verified
statically only — no JS test harness in this project). Minor cleanups logged
in `.superpowers/sdd/progress.md`: a `DOCUMENT_TOOL_NAMES` constant to dedup
the document-tool name set in `evidence.py`, and `tests/test_crux_helpers.py`
crossing the ~500-line guideline (507).

**#42 order-independent contribution merging** lives in
`consensus/methods/parsing.py` (`word_overlap_ratio`, `cluster_by_similarity`,
`canonical_index`, `cluster_text_contributions`), adopted by `record_ideas`,
`record_thoughts`, `record_options`, `record_criteria`; grouping is
connected-components (transitive, order-independent) and labels are the
cluster medoid. Suite after #42: **2324 passing**.

**#29 same-model panel warning** (this branch, PR #47) — new pure module
`consensus/methods/panel_diversity.py` (analysis core, `estimator_models`
roster adapter, `format_setup_warning` / `format_conclusion_disclosure`);
declarative `DiscussionMethod.assumes_independent_panel` flag +
`panel_composition_disclosure` helper (base.py), opted in by `DelphiMethod`
and `BeliefDiffusion`. `get_state()` emits a non-blocking `panel_advisory`
(inline setup banner + start toast); the two methods' conclusion prompts
disclose panel composition so convergence claims can be caveated. Trigger:
one model covers **> half** the AI estimator panel (`DIVERSITY_WARN_FRACTION`);
moderator excluded from the panel. Spec/plan:
`docs/superpowers/{specs,plans}/2026-07-15-same-model-panel-warning*.md`.
Suite after #29: **2355 passing**. Deferred: family-level model grouping
(exact-model only), and proposal item 3 (a "diversify" auto-suggest helper).

## Open work

### Cross-cutting quality

- Same-model panel warning shipped in two slices — #29 (PR #47, the warning)
  and #48 (PR #49, participating same-model moderator counted as an estimator).
  Remaining deferred follow-ups (no issue filed): family-level model grouping
  (e.g. `gpt-4o` vs `gpt-4o-mini`, or one model under different provider name
  strings — exact-model grouping only today); and the "diversify" auto-suggest
  helper (proposal item 3). Specs/plans:
  `docs/superpowers/{specs,plans}/2026-07-15-same-model-panel-warning-design.md`
  and `docs/superpowers/{specs,plans}/2026-07-16-panel-moderator-estimator*.md`.

### Method-specific follow-ups (tech debt, no issue filed)

- **ToT expansion refines in place; it cannot spawn child thoughts.** True
  Tree-of-Thoughts expands survivors into new candidate children; #26's
  "deep-dive" was implemented as refinements + obstacles on immutable
  thoughts (label stability is what makes re-scoring/convergence meaningful).
  If real transcripts show the beam starving, a child-generation expand
  variant (new thoughts with fresh ids) is the natural extension.
- **Double Crux belief shift is only measured for crux authors, and
  initial/final beliefs can refer to different phrasings.** `initial_beliefs`
  is snapshotted from the moderator's *selected* cruxes, so a participant
  whose crux wasn't selected shows `? → final`. The initial belief is on the
  author's own phrasing while `crux_belief` is on the moderator's synthesized
  claim — a polarity flip/reframe compares different propositions (a prompt
  instructs the moderator to preserve polarity, but that is not a guarantee).
  An optional pre-testing belief poll on the shared claim (one structured
  micro-turn after identification) would fix both — decide if the extra turn
  earns its cost.
- **Double Crux identify loop re-runs positions' context, not the phase.**
  Loop-backs re-enter `hunt_cruxes` only; if hunting keeps failing because
  positions were vague, there is no path back to `positions`. Acceptable for
  now; revisit if transcripts show otherwise.
- **MCDA free-text weights only parse the `(weight: N)` suffix.**
  `extract_weighted_criteria` recognises `1. Name (weight: 4)` / `[weight = 4]`;
  weights written in prose fall back to `DEFAULT_WEIGHT` silently. Fine for
  the AI path (structured tool enforces weights); a UI hint for humans would
  close the gap.

### Shared-helper dedup (low priority)

- **Balanced-brace inline-JSON scanner has one shared home
  (`methods/parsing.extract_json_payload`) but two older copies remain:**
  `_mcda_helpers.extract_scores` and `evaluate_matrix._parse_ratings`. Both
  should delegate (minor deltas: they return `{}` not `None`, dict-only). The
  shared scanner still miscounts braces inside JSON strings — accepted,
  documented in one place.
- **`record_thoughts`/`record_ideas` now both delegate to
  `parsing.cluster_text_contributions`** for the merge/cluster step, so only
  their give-up/validation blocks remain near-duplicated
  (`validate_thoughts_payload` vs `validate_ideas_payload`, and the ToT
  propose give-up block still mirrors `generate_ideas.py`). `record_criteria`
  inlines the same clustering skeleton rather than delegating, since it also
  aggregates `weight_votes` per cluster. A shared parametrised helper for the
  give-up/validation shape would keep those fixes in sync.
- ~~Structured payload validators share a fragile string-coercion pattern~~
  **(fixed 2026-07-16).** The 23 `str(payload.get(x, "")).strip()` sites turned
  a JSON `null` into the literal string `"None"`; they now delegate to
  `parsing.coerce_str(payload, key)` (public, since it is imported across 15
  phase modules), which treats both `None` and an absent key as the default.
  The safe `str(payload.get(x) or "")` sites were left untouched. Code review
  caught one same-class site outside the `payload.get` pattern —
  `str(v.get("rationale", ""))` on a nested vote item in `vote.py`, unguarded by
  its validator — now also on `coerce_str` (a `null` rationale rendered as
  `"None"` in the vote display).

### Testing gap — closed 2026-07-16

- ~~No real-pipeline (`complete_turn`) end-to-end flow test for the four
  newest methods.~~ **Done:** `tests/test_method_flow_e2e.py` +
  `tests/flow_e2e_helpers.py` drive NGT, MCDA, Double Crux, and ToT
  start→`method_complete` through `submit_human_message`/`complete_turn`
  (all-human, free-text path, no stubs), including the Double Crux
  identify→hunt loop-back and a full ToT score→prune→expand→score loop
  ending in convergence — both through the real `advance_phase` path.
  Spec: `docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md`.

### UX gap (older follow-up)

- **Blocked Triage switch still auto-concludes the discussion.** When
  `switch_discussion_method` rejects a handoff (non-tool-capable model),
  Triage falls through to `method_complete` and the discussion ends. A
  "reassign model and retry" UX (pause, let the user swap the offending
  participant's model, retry the switch) would beat ending outright. No
  mechanism exists yet.

## Conventions and gotchas for the next session

- **Structured-phase conversions must keep `process_response`.** Humans type
  free text, and the structured path falls back to it after exhausted
  retries. The fallback rarely *extracts* anything (rewritten prompts no
  longer describe the JSON-block format); the real containment is each
  phase's give-up cap (`MAX_FRAMING_ATTEMPTS`, `MAX_VOTE_ROUNDS`,
  `phase_round` advancement).
- **Every condition-based phase (`rounds=0`) needs a give-up cap** so an
  unparseable group cannot loop forever (`MAX_*_ATTEMPTS` / `MAX_*_ROUNDS`
  constants — no magic numbers, per `docs/llm/golden_rules.md`).
- **Structured conversions include a required `reasoning` field**, rendered
  before the data display so a validated payload reads as a real contribution.
  Exceptions: `submit_beliefs` declares `reasoning` optional; `submit_claims`
  has none (its `preliminary_conclusion`, like `submit_skeleton`'s
  `rich_summary`, plays that role for moderator extraction phases).
  Dynamic-key maps (belief distributions keyed by hypothesis label, matrix
  ratings keyed by hypothesis × evidence) declare `additionalProperties` in
  their schema rather than enumerating keys (see `MATRIX_TOOL_PARAMETERS` in
  `evaluate_matrix.py`, `BELIEFS_TOOL_PARAMETERS` in `_belief_helpers.py`).
- **Never derive a phase turn order from the incoming `entity_ids` by
  filtering the current order.** Handlers receive the full roster; for
  "everyone except X", filter the roster.
- **`method_state` keys starting with `_` are internal bookkeeping**
  (`_turn_order`, `_panelist_map`, `_continuation_count`,
  `_original_max_rounds`, `_original_cost_limit`, `_phase_entries`). New
  bookkeeping that must survive a method switch has to be added to the
  preserved set in `app_discussion_flow.switch_discussion_method`.
- **Moderator summaries never pass through `process_response`.** To capture
  something from the moderator, give that phase `get_turn_order ->
  [moderator_id]` so the moderator takes a real turn (see
  `counterfactual_extract.py`, `distill_skeleton.py`, `frame_hypotheses.py`,
  including bounded retries).
- **All beam/composite/weight/sensitivity/shift numbers are computed in
  code, never by the model.** Structured phases collect raw data; helper
  modules aggregate. Keep it that way — it is the correctness contract for
  every scored method.
- **Test new flow behavior through the real pipeline.** The historical
  failure mode was unit tests feeding handlers idealized inputs the moderator
  never produces. Use `tests/test_turn_order_flow.py` /
  `tests/test_method_state_persistence.py`: drive `complete_turn` with a human
  moderator plus `moderator_summary` (no network), and `Moderator._format_messages`
  for context filtering. For structured turns, stub `complete_with_tools`
  (see `tests/test_structured_output.py`).
- Project rules: `uv` only (never pip), TDD (failing test first), files under
  ~500 lines, docstrings + type hints mandatory.

## Decisions from the repo owner

- **#23 (2026-07-12): it is acceptable to require tool-capable models for
  methods with structured phases.** The regex fallback need not stay
  first-class — the design forces tool calls and surfaces a clear setup-time
  error (not a silent degrade) when a participant's model/provider lacks tool
  support.
- **Open Discussion is recommendable** (2026-07-12, executed with #24):
  `_EXCLUDED_METHODS = {"triage"}`.
