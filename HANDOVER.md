# HANDOVER

_Last updated: 2026-09-23. `main` is at **v2.0.0** (released 2026-07-20) with
the suite at **2872 passing**. The discussion-method review & repair campaign
(#12–#48, #56–#60) is finished and merged; so is alpha/stable distribution
(PyPI `consensus-app` + notarized macOS DMG), the public website, the
flow-error-visibility work (#71–#74, PR #76), and the tools_document
failure-visibility work (#78) — contracts for both summarised below.
The one open structural issue is **#61** (modules over the ~500-line golden
rule): `app_discussion_flow` and `tools_document` are split, the #78 work
added four more compliant modules on top of that, and fourteen modules
remain over the limit._

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
| Tests | 2872 passing (`uv run pytest`, ~55 s) |
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
- **Document failure visibility** (#78) — ten pre-existing defects in
  `tools_document/` the #61 split review exposed, all sharing one shape: a
  failure path indistinguishable from success. All fixed; see the section
  after flow error visibility for the contracts they established. Structural
  dividend: four new compliant modules (`errors.py`, `validation.py`,
  `background.py`, `handlers_rag.py`).

## Flow error visibility (#71–#74, merged — keep these contracts)

Three flow paths used to swallow caught errors. The repairs established
contracts worth knowing before touching `app_discussion_flow` again; the
narrative is in git history and PR #76.

- **Failures are classified into three kinds, not two.**
  `helpers.classify_flow_error` is the single classifier, returning
  `ERROR_KIND_PROVIDER` / `ERROR_KIND_CONFIG` / `ERROR_KIND_INTERNAL`;
  `is_provider_error` is a thin predicate over it. `internal` is the default
  bucket and only it asks the user to file a bug, so a misclassification is
  never merely cosmetic. Two rules keep it honest: catch `ConnectionError`,
  never its `OSError` base (a document tool reading a missing file also raises
  `OSError`, and that is our fault); and **type the fault where it happens** —
  `AIClient` raises `AIResponseFormatError` (`consensus/ai_response.py`) at
  its parse sites, because a gateway answering HTTP 200 with an HTML body
  otherwise surfaces as a bare `KeyError` reported to the user as our bug.
  `models.ConfigurationError` marks "this deployment is set up wrong".
  **Add new failure modes to the classifier, not to the notice wording** —
  notices live in `turns._SKIP_NOTICES` keyed by kind, and the toast wording
  in `static/turn-notices.js` is keyed by the result's `error_kind`.
- **`helpers.post_notice` is how a notice reaches the transcript.** It appends
  the in-memory `Message` first and wraps `db.add_message` in try/except: when
  the DB is what failed, an unguarded write raised a *second* exception out of
  the handler and the user saw nothing. It returns `(message, persisted)` and
  on a failed write appends `UNSAVED_NOTICE_SUFFIX`, since a notice that
  vanishes on reload is the same silent failure one step later. Callers
  surface the flag as `notice_unsaved`.
- **`conclude_discussion` returns `conclusion_error`** and posts a system
  notice; the discussion still concludes, deliberately. A `synthesis_shown`
  flag picks `_CONCLUSION_NOT_SAVED_NOTICE` over `_CONCLUSION_FAILURE_NOTICE`,
  because "could not be generated" is false when the synthesis sits directly
  above the notice. `mediate` posts an equivalent notice.
- **A failed Triage recommendation is never presented as a recommendation.**
  `MethodRecommender.recommend` **raises `RecommenderError`** (provider
  failure, unparseable reply, all-excluded catalog) and callers own the
  fallback — it previously caught everything itself and returned a plausible
  stand-in, so the error handler above it was unreachable and a crash reached
  users as a considered 50%-confidence pick. Both failure paths in
  `run_triage_recommender` go through `_record_recommender_failure`; key
  resolution and client construction are *inside* the `try`, and the `finally`
  close is itself guarded.
- **Non-UI consumers must not drop the error either.**
  `mcp_server.run_discussion` returns `conclusion_error` as its own field and
  matches the synthesis on `MessageRole.MODERATOR`, not a substring scan;
  `evaluation/runner` sets `result.error`.
- **Every new result key has a consumer.** `error_kind`, `notice_unsaved`,
  `conclusion_error` and `recommender_error` are each read by production code.
  A key that only tests read is a contract nobody honours.
- Golden rule 5 holds for these paths: retries with exponential backoff live
  one layer down in `ai_client._post_with_retry`, so the flow layer sees only
  exhausted failures.

**Testing lesson worth keeping.** The unreachable-#72 defect stayed green
because the tests patched `MethodRecommender.recommend` with
`AsyncMock(side_effect=...)` — asserting a raising contract the real class did
not have. When a test mocks the thing whose behaviour it is meant to be
testing, it proves nothing; mock one layer further out (the `AIClient`).
Contracts are covered by `tests/test_flow_error_visibility.py`,
`tests/test_flow_error_classification.py`,
`tests/test_flow_triage_recommender.py` and
`tests/test_flow_moderator_actions.py`.

## Document failure visibility (#78, merged — keep these contracts)

Ten defects in `tools_document/`, all sharing one shape: an error became
plausible-looking content, was returned in a non-error `ToolResult` (which
the UI renders with a ✅), was read by the AI as fact, and in one case was
persisted to the database permanently. Narrative is in git history; the
contracts below are what to keep before touching this package again.

- **Errors are typed at the fault site and become `ToolResult(is_error=True)`
  only at the handler boundary.** `tools_document/errors.py` (new):
  `DocumentError` base with `DocumentParseError`, `DocumentInterpretationError`,
  `DocumentIndexError`, each carrying an actionable `hint`. No module below
  the handlers formats an exception into prose — this mirrors what #71–#74
  established for the flow layer.
- **A failed summary is never persisted.** Migration
  `015_document_summary_status.sql` adds `documents.summary_status`
  (`'ok'`/`'failed'`/`'pending'`); `summary` holds only real summaries,
  `summary_status` says why one is missing, and `doc_list` reports that
  instead of reprinting an LLM error to every participant forever.
- **Background passes retain their task and their exception.**
  `consensus/background.py` (new): `spawn_background(coro, description)`
  keeps a strong reference AND retrieves/logs the task's exception; adopted
  by both `tools_document/embedding.py` and `tools_memory.py` — the same
  defect existed in both (golden rule 1). `tools_memory.py` dropped
  666 → 659 lines with its duplicate spawn helper gone.
- **A permanent indexing failure is reported as a failure, not "still
  indexing."** Per-document `_indexing_failures` state; `doc_ask` returns
  `is_error=True` with the embedder's real message after a failed pass, and
  posts a transcript notice via `app_discussion_flow.helpers.post_notice`,
  once per failure streak. **The failure marker must be evicted when the
  document becomes healthy again**, or "one notice per streak" silently
  degrades into "one notice ever."
- **Retrieval has a relevance floor and reports dimension mismatches as
  errors, not silence.** `_rank_by_similarity` returns a `RankingResult`
  (ranked rows + a dimension-mismatch count); `doc_ask` applies
  `MIN_SIMILARITY_THRESHOLD` (as `doc_list` already did) and distinguishes
  "nothing relevant" (non-error) from "these chunks need re-indexing" (error
  naming both dimensions).
- **`chapter_range` — not `extract_sections` — owns chapter extent.**
  `validation.chapter_range()` scans to the next header at the
  same-or-higher level, so a chapter carries its subsections;
  `extract_sections` still ends a section at the next header of *any* level
  because chunk boundaries need that. Changing `extract_sections` would
  invalidate every stored `sections_json`.
- **Range validation is shared, not duplicated.** `tools_document/validation.py`
  (new) `resolve_range()`, used by both `doc_get_text` and `doc_summary`,
  closing an inconsistency where only the latter guarded against a
  model-supplied out-of-range value.
- **Failed extraction raises; it does not manufacture content.**
  `parse_document` returns `ParsedDocument(markdown, fidelity, notes)`; a
  scanned PDF raises (naming OCR as the remedy) instead of ingesting as the
  string `"(Empty PDF)"`; the HTML regex fallback is logged and marked
  `degraded`; binary content raises instead of decoding to mojibake.
- **`fetch_url_content` retries transient failures and enforces the byte cap
  on both sides.** Exponential backoff on timeouts/transport errors/5xx (4xx
  raises immediately); `MAX_DOCUMENT_BYTES` is enforced on both the declared
  `content-length` and the actual body.
- **Two known, deliberate user-visible behaviour changes** (not bugs — record
  them honestly): a legitimately non-UTF-8-encoded text document (Latin-1,
  GBK, Shift-JIS) is now rejected rather than ingested as mojibake (charset
  detection would be the better answer — a genuine follow-up); a scanned/
  image-only PDF is now rejected naming OCR as the remedy, where it
  previously ingested as the string `"(Empty PDF)"`.

**Structural dividend for #61.** `handlers.py` went 541 → 348 (on top of the
earlier #61 split's 470); new `handlers_rag.py` (374, holds
`doc_ask`/`doc_summary`), `errors.py` (47), `validation.py` (83),
`background.py` (57) — all four compliant on arrival.

**Known gaps, recorded as follow-ups, not fixed here:** no test covers the
retry-exhaustion path in `fetch_url_content`; no test covers the combined
"some rows dimension-mismatched AND the rest below threshold" case in
`doc_ask`; a summary regeneration path is still absent now that
`summary_status='failed'` is recordable; OCR for scanned PDFs is named as a
remedy but not provided.

Contracts are covered by `tests/test_tools_document_failures.py`,
`tests/test_background.py`, and the existing `tests/test_tools_document*.py`
suite.

## Open work

### Issue #61 — modules over the ~500-line golden rule (in progress)

**Done — two slices.**

1. `app_discussion_flow.py` (1254) → `app_discussion_flow/` — `helpers.py`
   (287), `submissions.py` (304), `turns.py` (499), `method_switch.py` (421),
   `conclusion.py` (203), `__init__.py` (94). The only logic change was
   extracting `complete_turn`'s ~90-line Triage-handoff branch into
   `method_switch.handle_triage_handoff` (AST-verified identical); everything
   else moved verbatim. `_run_triage_recommender` became public when the split
   gave it a second consumer across a module boundary.
   **Watch `turns.py` (499) and `method_switch.py` (421)** — the error-
   visibility rounds grew both. The next addition to `turns.py` crosses the
   limit; split at that moment, not later. (These counts drift: the figures
   recorded here at the time of the split were stale within two PRs. Re-measure
   with `wc -l` before trusting them.)
2. `tools_document.py` (1277) → `tools_document/` — `constants.py` (22),
   `parsing.py` (144), `chunking.py` (98), `embedding.py` (177),
   `schemas.py` (137), `llm.py` (48), `ingestion.py` (115), `handlers.py`
   (456), `provider.py` (138), `__init__.py` (59). Pure move: every range
   verified byte-identical. The only new lines are the module headers and two
   `.` → `..` in-function `tools_memory` imports; the only removed lines are
   two module-level imports that were already dead in the original (`time`,
   `resolve_api_key`). A follow-up commit on the same PR then acted on the
   review: the tuning values that were left inline moved into `constants.py`
   (golden rule 3), the thrice-duplicated summary-snippet expression became
   `handlers._summary_snippet`, and the dead `LLM_TIMEOUT` was wired into
   `llm.py` — it equals `ai_client.DEFAULT_API_TIMEOUT`, so that is a no-op
   today, but the package's timeout is now tunable on its own. `handlers.py`
   is 470 after this; still under the limit.

   The review also surfaced a cluster of **pre-existing** defects in this
   package, deliberately left untouched here because fixing them would have
   destroyed the byte-identity property the safety argument rests on. They
   were fixed as **issue #78**, splitting `handlers.py` further (470 → 348,
   with `handlers_rag.py`, `errors.py`, `validation.py` and
   `consensus/background.py` born compliant) — see the "Document failure
   visibility" section above for the contracts.

**Facade guards.** Both packages have one, and they exist because a dropped
re-export fails *late*: `ConsensusApp` reaches flow functions by attribute
access at call time (`AttributeError`), and imports the document names lazily
inside method bodies. For `ingest_document` and `fetch_url_content` that is an
`ImportError` on first call; for `create_document_provider` it is worse than
late, it is *silent* — that import sits inside `_init_document_tools`'
`try: ... except ImportError`, so a dropped re-export just logs INFO ("Document
tools not available") and the eight `doc_*` tools never register. Neither is
caught at collection — deleting five flow re-exports once left the whole suite
green. `tests/test_app_discussion_flow_facade.py` and
`tests/test_tools_document_facade.py` pin `__all__` (by identity, not merely
existence) and AST-scan every module under `consensus/` for the call sites.
Keep them in step when the public API changes — that is the point of the pin,
and each was verified to fail when a name is removed.

**Two further slices came out of the error-visibility work**, both because the
additions pushed a previously-compliant file over the limit — refactor at the
moment you cross it, not later: `consensus/ai_response.py` (73) holds the pure
completion-body parsing helpers lifted out of `ai_client.py` (472), and
`consensus/static/turn-notices.js` (38) holds the skip-notice wording lifted
out of `discussion-actions.js` (494).

**Still over the limit** (`find consensus -name '*.py' | xargs wc -l | sort -rn`):

| Lines | File |
|------:|------|
| 1227 | `consensus/server.py` |
| 1200 | `consensus/app.py` |
| 784 | `consensus/auth.py` |
| 775 | `consensus/moderator.py` |
| 710 | `consensus/mcp_server.py` |
| 679 | `consensus/evaluation/runner.py` |
| 659 | `consensus/tools_memory.py` |
| 628 | `consensus/desktop.py` |
| 614 | `consensus/tools_python.py` |
| 589 | `consensus/evaluation/eval_db.py` |
| 580 | `consensus/tools_image.py` |
| 530 | `consensus/evaluation/scorer.py` |
| 517 | `consensus/methods/base.py` |
| 509 | `consensus/app_discussion_setup.py` |

Structural only — no behaviour change — one module per PR, suite green before
and after.

**Next-best target: `consensus/app.py`** (1200) — the orchestrator already has
an established split pattern (`app_providers`, `app_entities`,
`app_discussion_setup`, `app_discussion_flow/`, `app_discussion_state`), so the
remaining groups follow it, and it is well covered by the existing suite.
`tools_memory.py` (659) is the easy one after that: module-level functions
under clear banners, like `tools_document` was.

**`server.py` is the awkward one — read this before picking it.** It is a
single 1170-line `launch_web()` whose middleware and ~35 handlers are all
closures over `session_manager`, `auth_manager`, `app` and the helpers. There
is no verbatim move available: extracting them means giving each domain group
a factory that takes an explicit context object, which is a behaviour-risking
refactor, not a structural one. Budget for it accordingly, and write the
missing route tests first.

**Recipe that worked, for the next slice:**
0. **Check coverage before trusting "suite green before and after."**
   `uv run --with pytest-cov pytest -q --cov=consensus.<module>
   --cov-report=term-missing` — `tools_document.py` was at **15%**, so the
   suite proved nothing about it and the net had to be built first (152
   characterization tests, 100% coverage, committed *before* a line moved).
   A split of an untested module is not verified by a green suite.
1. Move code by line range (`sed -n 'a,bp'`) so it transfers verbatim, then
   diff each moved range back against `git show HEAD:<file>` — every range
   should come out byte-identical, and every non-blank line of the original
   should be accounted for by some range.
2. Fix relative-import depth for anything nested one level deeper
   (`from .x` → `from ..x`), **including imports inside function bodies** —
   these are the ones that fail at runtime rather than at collection.
3. Re-export the public API from `__init__.py`; point test `patch()` targets
   at the *defining* submodule (patching the facade has no effect), and import
   private helpers from their submodule rather than widening the facade.
   `assertLogs`/`caplog` on the old dotted name keeps working — child loggers
   propagate to the package logger.
4. `uvx ruff check --select F <pkg>` for unused/undefined names, then the
   full suite.
5. Add the facade guard, then **delete a re-export and watch it fail** before
   restoring it. An unverified guard is not a guard. Pin `__all__` by
   *identity* (`td.parse_document is parsing.parse_document`), not by
   existence — a crossed re-export resolves fine and breaks only in
   production. Scan every module under `consensus/` for call sites, matching
   both the relative and absolute spelling of the package; scanning only
   `app.py` misses the next consumer someone adds.
6. **Mutate the new wiring and watch it fail.** The glue a split *writes* —
   the factory that forwards dependencies to handlers, the guard conditions
   duplicated across two call sites, the keyword names production passes — is
   the code most likely to be got wrong and least likely to be covered, since
   the characterization tests were written against the old shape. On this
   slice three such mutations survived the whole suite at 100% coverage.
   Coverage measures lines executed, not behaviour constrained.
7. Update the docs that name the old module file — `CLAUDE.md`, `README.md`,
   `docs/BUILTIN_TOOLS.md`, `docs/devel/01-getting-started.md`,
   `02-architecture.md`, `08-tool-use.md`, `programmer-manual.md` all carry
   module inventories. Check the *content* beside the name too: the `doc_*`
   parameter tables in `BUILTIN_TOOLS.md` and `08-tool-use.md` had been wrong
   for four of eight tools, in the very blocks earlier PRs edited to fix the
   module name.

**Writing the safety net, if the target is untested.** Mock one layer further
out than the code under test: real `Database` (the `tmp_db` fixture), fakes
only at the network edges. Shared fakes go in a `tests/*_helpers.py` module,
following `tests/flow_e2e_helpers.py`. `tests/document_helpers.py` has a
`patch_where_defined(monkeypatch, anchor, name, replacement)` that resolves
the target through `sys.modules[anchor.__module__]`, so the tests stay valid
across the split without naming module paths that are about to change — worth
copying for the next one.

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
