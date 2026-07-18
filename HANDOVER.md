# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-18 (structured-phase human input, issue #57, built on
branch `feat/structured-phase-human-input` — humans now get a schema-driven
input form in structured phases instead of having to type raw JSON; branch at
2495 tests, PR pending). Main at 2459 tests; the #57 branch is the only open
work.)._

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
| Method-flow E2E tests (NGT/MCDA/DC/ToT) | — (testing gap) | #52 |
| Alpha distribution (PyPI `consensus-app` + macOS DMG) | — | #53 |
| Shared-helper dedup batch (scanner delegation, give-up mixin, test split) | — (tech debt) | #54 |
| Blocked Triage switch recovery (pause + retry) | — (HANDOVER UX gap) | #55 |
| Double Crux pre-belief poll (belief-shift metric fix) | — (tech debt) | #56 |
| Structured-phase human input (form renderer) | #57 | pending |

Main is at **2459 tests passing**; the `feat/structured-phase-human-input`
branch (issue #57) is at **2495** and ready for PR. Every earlier
method-repair issue (#12–#48, #56) is merged and closed.

**Double Crux pre-belief poll** (PR #56, merged) — spec/plan under
`docs/superpowers/{specs,plans}/2026-07-17-double-crux-pre-belief-poll*.md`.
A structured micro-turn phase `poll_belief` (`consensus/methods/phases/
poll_belief.py`) runs after `identify_crux` and before `test_crux` on the
**factual path only** (identify's routing jumps `values`/`none` straight to
`resolve`). Every disagreeing party states its probability on the
moderator's *synthesized* shared claim, and that poll is the authoritative
`initial_beliefs` (the "before" end of the belief-shift metric). Poll
helpers live in `_crux_helpers.py`; artifact/formatting helpers in the
sibling `_crux_artifact.py`. **Always-on, factual-only** (owner decision);
total poll failure degrades to an honest `?`, never a fabricated number.
Each poller's numeric belief line is redacted from later pollers' context
(`PollBeliefHandler.filter_context_message` / `redact_belief_lines`) so the
baseline is not anchored by earlier numbers. The unparsed-human-input case
(prose instead of JSON) is the framework-wide gap tracked as **issue #57**.

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
statically only — no JS test harness in this project). The two minor
cleanups once logged in `.superpowers/sdd/progress.md` are done:
`DOCUMENT_TOOL_NAMES` shipped with the PR #45 review follow-ups, and
`test_crux_helpers.py` was split (artifact/formatting layer →
`test_crux_artifact.py`) in PR #54.

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

**Structured-phase human input** (issue #57, branch
`feat/structured-phase-human-input`, PR pending) — spec/plan under
`docs/superpowers/{specs,plans}/2026-07-17-structured-phase-human-input*.md`.
A human taking a turn in a `requires_structured_output` phase now gets a
**schema-driven input form** instead of having to type raw JSON.
- **Backend**: `submit_human_structured_message(entity_id, payload)`
  (`app_discussion_flow.py`, exposed via desktop bridge + server route +
  `api.submitStructuredMessage`) validates a payload (`check_payload_schema`
  in `methods/parsing.py`, then the handler's `validate_output`) and records
  via `process_structured_response`, mirroring the AI branch. A **safety net**
  in `submit_human_message` converts the old silent drop into a visible error
  (golden rule 6): a structured-phase free-text turn that records nothing and
  carries no schema-valid JSON block returns `{"error": ...}`.
- **State**: `get_state()` exposes `current_input_spec`
  (`consensus/structured_input.py` — `build_input_spec`/`schema_is_renderable`)
  for a human participant (not the moderator) in a structured phase.
  Dynamic-key schemas (belief maps) are resolved to concrete fields by the new
  `PhaseHandler.resolve_input_schema` hook (`expand_belief_schema`).
- **Frontend**: `consensus/static/structured-form.js` renders one widget per
  schema property (number/string/enum/boolean/array/array-of-objects) with a
  guided-JSON fallback for un-renderable (nested `additionalProperties`, e.g.
  the ACH matrix) schemas.
- **Gotcha closed during verification**: any lifecycle app method that returns
  `discussion.to_dict()` drops `current_input_spec` (a get_state-only field),
  so the form vanishes after that transition. `pause`/`resume`/`reopen` were
  fixed to return `get_state()`; `start`/`conclude`/`continue` already did.
  **New rule: lifecycle methods the frontend feeds to `onStateUpdate(result)`
  must return `get_state()`, never `to_dict()`.**
- Verified live (Playwright, all-human Delphi): form renders, validates
  required fields inline, records + advances, fresh form per turn, survives
  pause→resume. No phase is both structured and evidence-tracked, so the form
  needs no evidence affordance. Deferred (Minor, see git/ledger): client-side
  numeric-range backstop; optional-enum first-option default.

## Open work

### Alpha distribution — pending release gate (PR #53)

- **First DMG release is gated on notarization** (owner deferred 2026-07-17):
  one-time `xcrun notarytool store-credentials consensus-notary --apple-id …
  --team-id X5DWXB4283` (app-specific password from account.apple.com), then
  a full `scripts/build_macos_dmg.sh` run (no `--skip-notarize`), then an
  in-app `execute_python` acceptance test on the notarized app — the only
  runtime path never exercised end-to-end.
- Key facts: PyPI distribution name is **consensus-app** (import/CLI stay
  `consensus`); release via `scripts/release_pypi.sh` (`--build-only`,
  `--test` for TestPyPI, needs `UV_PUBLISH_TOKEN`); versioning is plain
  PEP 440 (`1.99.x`) — never pre-release tags (they'd force
  `--prerelease=allow` on testers). Spec/plan:
  `docs/superpowers/{specs,plans}/2026-07-16-alpha-distribution*.md`.
- Accepted follow-ups (no issue filed): `consensus/evaluation/runner.py`
  default results dir lands in site-packages for wheel installs;
  `packaging/macos/make_icns.sh` regeneration needs Pillow on system
  python3; icon bubbles blur at 16–32 px.

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
- **Double Crux belief shift** — ✅ fixed by the pre-belief poll (PR #56;
  see the feature note above). Both ends of the metric are now measured on
  the moderator's synthesized claim for every party.
- **Double Crux identify loop re-runs positions' context, not the phase.**
  Loop-backs re-enter `hunt_cruxes` only; if hunting keeps failing because
  positions were vague, there is no path back to `positions`. Acceptable for
  now; revisit if transcripts show otherwise.
- **MCDA free-text weights only parse the `(weight: N)` suffix.**
  `extract_weighted_criteria` recognises `1. Name (weight: 4)` / `[weight = 4]`;
  weights written in prose fall back to `DEFAULT_WEIGHT` silently. Fine for
  the AI path (structured tool enforces weights); a UI hint for humans would
  close the gap.

### Shared-helper dedup — closed 2026-07-17 (PR #54)

All three items are done; the shared homes to keep using are:

- **Inline-JSON scanning:** `methods/parsing.extract_json_payload` is the
  only balanced-brace scanner. `_mcda_helpers.extract_scores` and
  `evaluate_matrix._parse_ratings` delegate to it (dict-only, `{}` on
  failure — a fenced non-mapping value no longer leaks through). The
  scanner still miscounts braces inside JSON strings — accepted,
  documented in one place.
- **Give-up/validation shape:** `parsing.validate_string_list_payload`
  backs `validate_ideas_payload` / `validate_thoughts_payload` (messages
  stay at the call sites); the NGT/ToT generation give-up blocks live once
  in `phases/_generation_giveup.GenerationGiveUpMixin` (declarative
  `giveup_*` class attributes — use it for future bounded generation
  phases); `parsing.cluster_groups` is the clustering skeleton shared by
  `cluster_text_contributions` and MCDA's `record_criteria`.
- **Null-safe payload coercion:** `parsing.coerce_str(payload, key)`
  (fixed 2026-07-16; see PR #50).

### Testing gap — closed 2026-07-16

- ~~No real-pipeline (`complete_turn`) end-to-end flow test for the four
  newest methods.~~ **Done:** `tests/test_method_flow_e2e.py` +
  `tests/flow_e2e_helpers.py` drive NGT, MCDA, Double Crux, and ToT
  start→`method_complete` through `submit_human_message`/`complete_turn`
  (all-human, free-text path, no stubs), including the Double Crux
  identify→hunt loop-back and a full ToT score→prune→expand→score loop
  ending in convergence — both through the real `advance_phase` path.
  Spec: `docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md`.

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
- **`_pending_method_switch` is internal `method_state` bookkeeping,
  deliberately NOT in `switch_discussion_method`'s preserved set** — a
  successful switch must wipe it.
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
