# HANDOVER

_Last updated: 2026-09-22. `main` is at **v2.0.0** (released 2026-07-20) with
the suite at **2594 passing**. The discussion-method review & repair campaign
(#12–#48, #56–#60) is finished and merged; so is alpha/stable distribution
(PyPI `consensus-app` + notarized macOS DMG) and the public website. The four
defects that the #61 split surfaced — **#71**, **#72**, **#73**, **#74** —
are fixed in this session (see "Flow error visibility" below). The one open
structural issue is **#61** (modules over the ~500-line golden rule); its
first slice — `app_discussion_flow` — is done, fifteen modules remain._

This file briefs the next session on what is done, what is still open, and the
conventions to keep. Update it whenever a session materially changes the plan;
delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `docs/superpowers/specs/`, and
`docs/superpowers/plans/` — do not re-narrate it here.

## Where the project stands

| Area | State |
|------|-------|
| Discussion methods | 18 methods, all reviewed/repaired; composable `PhaseHandler` library (68 handlers) |
| Structured outputs | Forced tool calls for every structured phase; humans get a schema-driven form (#57) |
| Distribution | `consensus-app` on PyPI; notarized + stapled macOS DMG; v2.0.0 is the current stable |
| Website | `website/` — static site deployed to Cloudflare Pages at https://consensus-ai.org/ |
| Tests | 2594 passing (`uv run pytest`, ~55 s) |
| Docs | README, QUICKSTART, user manual and `docs/devel/` aligned with the code (PR #62) |

### Merged campaigns (detail in git history)

- **Method review & repair** — six defect classes (#12–#17), per-method fixes
  (#19/#20/#21), phase-machine loops (#22), Belief Diffusion abort (#30),
  structured outputs (#23), order-independent contribution merging (#42),
  same-model panel warning (#29/#48), Double Crux pre-belief poll (#56),
  blocked-Triage switch recovery, shared-helper dedup, `coerce_str` hardening.
- **New methods** — Nominal Group Technique (#24), Weighted Decision Matrix /
  MCDA (#25), Double Crux (#27), Tree of Thoughts (#26).
- **Evidence-tracked phases** (#28) — `consensus/evidence.py`, opt-in
  `Phase.track_evidence`; soft by design (annotate + log, never block) — see
  `memory/evidence-gating-philosophy.md`.
- **Structured-phase human input** (#57, #59, #60) —
  `submit_human_structured_message` + `consensus/static/structured-form.js`.
- **Method-flow E2E tests** — `tests/test_method_flow_e2e.py` drives NGT, MCDA,
  Double Crux and ToT start→`method_complete` through the real pipeline.
- **Provider resilience** (PR #64, #65) — `tool_choice` downgrade to
  `"required"` when a provider rejects a named function; any 400 naming an
  optional sampling parameter drops it and retries; `describe_turn_error()`
  surfaces the provider's response body in skip notices; moderator-summary
  failures toast instead of stopping the turn cycle silently; an empty
  completion renders an explanatory notice naming the `max_tokens` cap.
- **Distribution** — PyPI + DMG pipeline; notarization gate cleared at v1.99.1;
  v2.0.0 followed the full playbook. See
  `memory/alpha-distribution-release-process.md` for the release runbook.
- **Flow error visibility** (#71/#72/#73/#74) — the four pre-existing defects
  the #61 split exposed. All fixed; see the next section for the contracts
  they established.

## Flow error visibility (#71–#74, done — keep these contracts)

Three flow paths swallowed caught errors; the repairs added contracts worth
knowing before touching `app_discussion_flow` again.

- **Provider faults and internal bugs are now distinguished.**
  `helpers.is_provider_error` is the single classifier (`httpx.HTTPError`,
  `TimeoutError`, `ConnectionError`, `StructuredOutputError`); everything
  else is a bug in Consensus. `describe_turn_error` keeps its existing
  contract for provider faults, `describe_internal_error` always names the
  exception type (a bare `KeyError` renders as `'positions'`, which told a
  user nothing), and `describe_flow_error` dispatches between them. A skipped
  turn carries `error_kind: "provider" | "internal"` and a notice that says
  which it was — the old text blamed the provider for every failure, including
  handler and DB bugs. **Add new failure modes to the classifier, not to the
  notice wording.**
- **`helpers.post_notice` is how a notice reaches the transcript.** It appends
  the in-memory `Message` first and wraps the `db.add_message` in try/except.
  That ordering is the point: when the DB is what failed, an unguarded write
  raised a *second* exception out of the handler and the user saw nothing.
- **`conclude_discussion` returns `conclusion_error`** and posts a system
  notice; `ConsensusApp.conclude_discussion` merges the key into `get_state()`
  and the frontend toasts it. The discussion still concludes — deliberately;
  an expensive session must not be left half-ended.
- **A failed Triage recommendation is never presented as a recommendation.**
  Both failure paths in `run_triage_recommender` (exception, and a moderator
  with no `ai_config`) go through `_record_recommender_failure`, which writes
  `recommender_error` + empty `recommendations` + the
  `RECOMMENDER_FALLBACK_METHOD` fallback together. The function returns the
  detail string; `generate_ai_turn` appends it to the recommend-phase message,
  and `TriageConfirmHandler` prefixes its prompts with it. A later success
  pops `recommender_error`, so retries do not leave stale warnings.
  `TriageConfirmHandler.process_response` validates a backticked choice
  against the **whole registry** when the shortlist is empty, so the notice's
  "name the method you want" is actually actionable.
- **`tests/test_evidence_flow.py` now uses `open_discussion`** for its fake
  phase method. It used `double_crux`, which really has a tracked `test_crux`
  phase, so the stub was indistinguishable from the real method and the
  monkeypatches proved nothing (verified: repointing them at a module that
  intercepts nothing used to leave the file green, and now fails).
- Golden rule 5 was confirmed satisfied for these paths: retries with
  exponential backoff live one layer down in `ai_client._post_with_retry`
  (`MAX_RETRIES`, `RETRY_BASE_DELAY`), so the flow layer sees only exhausted
  failures.

Flow-package coverage went 89% → 97%; `conclusion.py` 59% → 100%. `mediate`,
the `reassign_turn` wrapper, moderator-summary cost attribution and the
turn/complete-turn guard branches now have tests
(`tests/test_flow_moderator_actions.py`); the error contracts are in
`tests/test_flow_error_visibility.py` and
`tests/test_flow_triage_recommender.py`.

## Open work

### Issue #61 — modules over the ~500-line golden rule (in progress)

**Done:** `app_discussion_flow.py` (1254 lines) → the `app_discussion_flow/`
package — `helpers.py` (133), `submissions.py` (304), `turns.py` (444),
`method_switch.py` (362), `conclusion.py` (137), plus a re-exporting
`__init__.py` (92) so `from consensus.app_discussion_flow import …` is
unchanged for `app.py` and the tests. The only logic change was extracting
`complete_turn`'s ~90-line Triage-handoff branch into
`method_switch.handle_triage_handoff` (AST-verified identical to the original
block); everything else moved verbatim.

**Guard added after review.** `ConsensusApp` reaches these functions by
*attribute access at call time* (`app_discussion_flow.mediate(...)`), so a name
dropped from `__init__.py` is an `AttributeError` on that route in production,
never an `ImportError` at collection. Deleting five re-exports left the whole
suite green, so nothing caught it. `tests/test_app_discussion_flow_facade.py`
now pins `__all__` and AST-parses `app.py` to assert every
`app_discussion_flow.X` call site resolves. Keep it in step when the public
flow API changes — that is the point of the pin.

`_run_triage_recommender` became `run_triage_recommender` when the split gave
it a second consumer across a module boundary (`turns` → `method_switch`).

**Still over the limit** (`find consensus -name '*.py' | xargs wc -l | sort -rn`):

| Lines | File |
|------:|------|
| 1277 | `consensus/tools_document.py` |
| 1227 | `consensus/server.py` |
| 1187 | `consensus/app.py` |
| 784 | `consensus/auth.py` |
| 772 | `consensus/moderator.py` |
| 702 | `consensus/mcp_server.py` |
| 671 | `consensus/evaluation/runner.py` |
| 666 | `consensus/tools_memory.py` |
| 628 | `consensus/desktop.py` |
| 614 | `consensus/tools_python.py` |
| 589 | `consensus/evaluation/eval_db.py` |
| 580 | `consensus/tools_image.py` |
| 530 | `consensus/evaluation/scorer.py` |
| 517 | `consensus/methods/base.py` |
| 509 | `consensus/app_discussion_setup.py` |

Structural only — no behaviour change — one module per PR, suite green before
and after. Next-best targets: `server.py` (routes group by domain, could follow
the `db/` mixin pattern) and `tools_document.py` (ingestion /
chunking+embedding / RAG Q&A are three separable concerns).

**Recipe that worked, for the next slice:**
1. Move code by line range (`sed -n 'a,bp'`) so it transfers verbatim, then
   diff each moved range back against `git show HEAD:<file>` — every range
   should come out byte-identical.
2. Fix relative-import depth for anything nested one level deeper
   (`from .x` → `from ..x`), including imports inside function bodies.
3. Re-export the public API from `__init__.py`; point test `patch()` targets
   at the *defining* submodule (patching the facade has no effect), and import
   private helpers from their submodule rather than widening the facade.
   `assertLogs`/`caplog` on the old dotted name keeps working — child loggers
   propagate to the package logger.
4. `uvx ruff check --select F <pkg>` for unused/undefined names, then the
   full suite.

### Deferred follow-ups (no issue filed)

- **Panel diversity** — family-level model grouping (exact-model only today,
  so `gpt-4o` vs `gpt-4o-mini` read as different estimators) and a "diversify"
  auto-suggest helper.
- **ToT expansion refines in place; it cannot spawn child thoughts.** Label
  stability is what makes re-scoring/convergence meaningful. If real
  transcripts show the beam starving, a child-generation expand variant is the
  natural extension.
- **Double Crux identify loop re-runs positions' context, not the phase.**
  Loop-backs re-enter `hunt_cruxes` only; there is no path back to `positions`
  if hunting keeps failing because positions were vague.
- **MCDA free-text weights only parse the `(weight: N)` suffix.**
  `extract_weighted_criteria` recognises `1. Name (weight: 4)` / `[weight = 4]`;
  weights written in prose fall back to `DEFAULT_WEIGHT` silently. The AI path
  is safe (the structured tool enforces weights); a UI hint would close the gap
  for humans.
- **#28 follow-ups** — per-claim citation mapping; opting in Adversarial Collab
  `gather_evidence` and ACH `present_evidence`; a richer source-picker UI; a
  live browser click-through of the Attach-evidence button (verified statically
  only — there is no JS test harness in this repo).
- **Packaging** — `consensus/evaluation/runner.py`'s default results dir lands
  in site-packages for wheel installs; `packaging/macos/make_icns.sh`
  regeneration needs Pillow on system python3; icon bubbles blur at 16–32 px.
  (Fixed here: `scripts/release_pypi.sh` cleaned `dist/` but not `build/`,
  setuptools' staging tree, so a module deleted or moved since the last build
  survived in `build/lib` and was packaged into the new wheel alongside its
  replacement — reproduced with `app_discussion_flow.py` during this split.
  The clean now covers `build/` and `*.egg-info/` too, and because the wheel
  checks were presence-only — a wheel shipping *both* `X.py` and `X/` passed
  every one of them — `check_no_shadowed_packages` asserts no name ships as
  both. Verified by injecting a stale `app_discussion_flow.py` into a built
  wheel: the check fails as intended.)

### Roadmap

`ROADMAP.md` holds the planned feature list. The nearest ⬜ items by payoff are
Argument Mapping and Tournament / Superforecasting (both Medium), plus
token-aware context windowing and lazy discussion message loading.

## Conventions and gotchas for the next session

- **Structured-phase conversions must keep `process_response`.** Humans type
  free text, and the structured path falls back to it after exhausted retries.
  The real containment is each phase's give-up cap (`MAX_FRAMING_ATTEMPTS`,
  `MAX_VOTE_ROUNDS`, `phase_round` advancement).
- **Every condition-based phase (`rounds=0`) needs a give-up cap** so an
  unparseable group cannot loop forever (`MAX_*_ATTEMPTS` / `MAX_*_ROUNDS`
  constants — no magic numbers, per `docs/llm/golden_rules.md`).
- **Structured conversions include a required `reasoning` field**, rendered
  before the data display so a validated payload reads as a real contribution.
  Exceptions: `submit_beliefs` declares it optional; `submit_claims` has none.
  Dynamic-key maps (belief distributions, matrix ratings) declare
  `additionalProperties` rather than enumerating keys.
- **Never derive a phase turn order from the incoming `entity_ids` by
  filtering the current order.** Handlers receive the full roster; for
  "everyone except X", filter the roster.
- **`method_state` keys starting with `_` are internal bookkeeping**
  (`_turn_order`, `_panelist_map`, `_continuation_count`,
  `_original_max_rounds`, `_original_cost_limit`, `_phase_entries`). New
  bookkeeping that must survive a method switch has to be added to the
  preserved set in `switch_discussion_method`. `_pending_method_switch` is
  deliberately NOT preserved — a successful switch must wipe it.
- **Moderator summaries never pass through `process_response`.** To capture
  something from the moderator, give that phase `get_turn_order ->
  [moderator_id]` so the moderator takes a real turn (see
  `counterfactual_extract.py`, `distill_skeleton.py`, `frame_hypotheses.py`).
- **All beam/composite/weight/sensitivity/shift numbers are computed in code,
  never by the model.** Structured phases collect raw data; helper modules
  aggregate. It is the correctness contract for every scored method.
- **Test new flow behavior through the real pipeline.** The historical failure
  mode was unit tests feeding handlers idealized inputs the moderator never
  produces. Use `tests/test_turn_order_flow.py` /
  `tests/test_method_state_persistence.py`: drive `complete_turn` with a human
  moderator plus `moderator_summary` (no network). For structured turns, stub
  `complete_with_tools` (see `tests/test_structured_output.py`).
- **Lifecycle methods the frontend feeds to `onStateUpdate(result)` must
  return `get_state()`, never `to_dict()`** — `to_dict()` drops get_state-only
  fields such as `current_input_spec`, so the structured form vanishes after
  the transition (bit `pause`/`resume`/`reopen` during #57).
- **Human turn submissions share one precondition gate.** Both
  `submit_human_message` and `submit_human_structured_message` route through
  `_check_human_turn_preconditions`, which rejects an unknown entity, a
  **concluded** discussion, and a wrong-turn submission. Any new human submit
  entry point must go through it (issue #59). `submit_moderator_message` is
  intentionally separate — it has no turn/status gate.
- **A paused discussion still accepts free-text human input** (issue #60 — do
  not "tighten" this into an `is_active` rejection without changing the UI in
  the same commit). The composer stays open while paused, and the paused branch
  posts to `submit_human_message`. The two paths diverge **deliberately**:
  - `submit_human_message` records the message but skips the
    `get_active_method` / `process_response` block while paused. The turn does
    not advance, so the composer accepts repeated sends, and re-running
    `process_response` would let a non-idempotent handler double-record a
    vote. The real contribution is processed once, on the turn after Resume.
  - `submit_human_structured_message` keeps a full `is_active` check: a
    structured payload writes into `method_state`, so it is unambiguously a
    turn — and the form is never mounted while paused anyway.
- Project rules: `uv` only (never pip), TDD (failing test first), files under
  ~500 lines (issue #61), docstrings + type hints mandatory.

## Decisions from the repo owner

- **#23 (2026-07-12): it is acceptable to require tool-capable models for
  methods with structured phases.** The regex fallback need not stay
  first-class — the design forces tool calls and surfaces a clear setup-time
  error (not a silent degrade) when a participant's model lacks tool support.
- **Open Discussion is recommendable** (2026-07-12): `_EXCLUDED_METHODS =
  {"triage"}`.
- **Evidence gating annotates, never blocks** — see
  `memory/evidence-gating-philosophy.md`.
