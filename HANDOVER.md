# HANDOVER — Discussion Methods Review & Repair

_Last updated: 2026-07-17 (alpha distribution merged via PR #53 — PyPI
package `consensus-app` + signed macOS DMG build pipeline; shared-helper
dedup batch via PR #54 closes this file's dedup list; blocked Triage
switch recovery (pause + retry) merged via PR #55 closes the older
"blocked Triage switch auto-concludes" UX gap; Double Crux pre-belief
poll (branch `feat/double-crux-pre-belief-poll`, PR pending) closes the
belief-shift metric tech-debt item below. All tracked issues closed.
Main at 2454 tests.)._

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
| Double Crux pre-belief poll (belief-shift metric fix) | — (tech debt) | (PR pending) |

Main is at **2454 tests passing**. Every tracked issue is merged and closed.
The Double Crux pre-belief poll is on branch
`feat/double-crux-pre-belief-poll` with a PR pending review; there are no
other open issues or PRs.

**Double Crux pre-belief poll** (branch `feat/double-crux-pre-belief-poll`)
— spec `docs/superpowers/specs/2026-07-17-double-crux-pre-belief-poll-design.md`,
plan `docs/superpowers/plans/2026-07-17-double-crux-pre-belief-poll.md`. A
new structured micro-turn phase `poll_belief` (`consensus/methods/phases/
poll_belief.py`) runs after `identify_crux` and before `test_crux`, on the
**factual path only** (identify's routing already jumps `values`/`none`
straight to `resolve`). Every disagreeing party states their probability on
the moderator's *synthesized* shared claim; that poll becomes the
authoritative `initial_beliefs` (the "before" end of the belief-shift
metric), fixing the two defects the old hunt-snapshot had: non-crux-authors
showed `? → final`, and the initial (author phrasing) vs final (moderator
claim) compared different propositions. Poll helpers live in
`_crux_helpers.py` (`MAX_POLL_ROUNDS`, `POLL_BELIEF_TOOL_PARAMETERS`,
`validate_poll_belief_payload`, `record_poll_belief`, `entities_with_poll`,
`extract_poll_belief`, `apply_poll_beliefs`). `record_crux_selection` no
longer snapshots beliefs (poll owns `initial_beliefs`; per-crux `belief` is
kept as provenance). **Always-on, factual-only** (owner decision); total
poll failure degrades to an honest `?`, never a fabricated number. Whole-
branch review (opus): ready to merge, no Critical/Important. Deferred
follow-ups: (1) `_crux_helpers.py` reached 574 lines — split
`build_crux_map`/`format_*` into a `_crux_artifact.py` sibling when next
touched; (2) sequential poll turns leave earlier pollers' beliefs visible in
later pollers' context (identical to hunt/resolve; spec only committed to
prompt-level non-anchoring) — add a `filter_context_message` redaction only
if true anchoring-immunity is wanted; (3) no `values`/`none` E2E asserting
the poll is skipped (routing is deterministic + unit-covered).

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
- **Double Crux belief shift** — ✅ fixed by the pre-belief poll (branch
  `feat/double-crux-pre-belief-poll`, PR pending; see the feature note
  above). Both ends of the metric are now measured on the moderator's
  synthesized claim for every party.
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
