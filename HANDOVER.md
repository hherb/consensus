# HANDOVER

_Last updated: 2026-11-07. `main` is at **v2.0.0** (released 2026-07-20) plus
the merged #61 slices and the #104 rotation fix (`fix/rotation-sync-104`,
suite at **3320 passing**). The
discussion-method review & repair campaign (#12–#48, #56–#60) is finished and
merged; so is alpha/stable distribution (PyPI `consensus-app` + notarized macOS
DMG), the public website, the flow-error-visibility work (#71–#74, PR #76), and
the tools_document failure-visibility work (#78) — contracts for both
summarised below. The one open structural issue is **#61** (modules over the
~500-line golden rule): `app_discussion_flow`, `tools_document`, `app.py`
(→ `app_api/` mixins), `tools_memory`, `tools_python`, `tools_image` and
`methods/base.py` and `app_discussion_setup.py` are split; eight modules remain over the limit. **#86** (lint gate) is done: zero `ruff --select F` findings in
`consensus/`, enforced by `tests/test_lint.py`._

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
| Website | `website/` — static site on Cloudflare Pages at https://consensus-ai.org/, incl. a discussion-methods catalogue (`methods.html`, PRs #82/#83; `_headers` cache-busts CSS) |
| Tests | 3320 passing (`uv run pytest`, ~100 s; runs in `.venv` via the `dev` group) |
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
- **Provider resilience** (PR #64, #65) — `tool_choice` / sampling-parameter
  400 downgrades in `ai_client._post_with_retry`; provider bodies surface in
  skip notices (see ROADMAP "Provider API error resilience").
- **Distribution** — PyPI + notarized DMG; runbook in
  `memory/alpha-distribution-release-process.md`.
- **Flow error visibility** (#71–#74) and **document failure visibility**
  (#78) — pre-existing defects the #61 splits exposed; contracts below.
- **Lint gate** (#86) — contract below.

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

## Document failure visibility (#78, PR #79 — keep these contracts)

Ten `tools_document/` defects shared one shape: an error became
plausible-looking content in a non-error `ToolResult` (rendered with a ✅),
was read by the AI as fact, and once was persisted. Narrative is in git
history; keep these before touching the package:

- **Errors are typed at the fault site** (`tools_document/errors.py`:
  `DocumentError` → `DocumentParseError` / `DocumentInterpretationError` /
  `DocumentIndexError`, each with an actionable `hint`) and become
  `ToolResult(is_error=True)` only at the handler boundary.
- **A failed summary is never persisted.** `documents.summary_status`
  (`models.SummaryStatus`: `ok`/`failed`/`pending`, migrations 015/016 —
  new versions, never edits to applied migrations).
- **Background passes go through `consensus/background.py`
  `spawn_background()`**, which keeps a strong reference and logs the
  task's exception.
- **A permanent indexing failure is reported, not "still indexing"**:
  `doc_ask` errors with the embedder's message and posts one transcript
  notice per failure streak. The marker **must be evicted** when the
  document recovers, and the failure branch **must still re-kick** the pass
  (gated by `INDEXING_RETRY_INTERVAL`) — returning early there was a
  regression only the whole-branch review caught.
- **Process-global bookkeeping is keyed by `dbkey.scoped_key(db, id)`**
  (`(db_path, id)`), never the bare id: in `--multi-user` every session's
  first row is id 1. This bit twice — in `tools_document` (#78) and again in
  `context_strategies` vs `tools_memory` (fixed in the #61 tools_memory slice).
- **Retrieval has a relevance floor** (`MIN_SIMILARITY_THRESHOLD`) and
  reports embedding-dimension mismatches as errors, not silence.
- **`validation.chapter_range()` owns chapter extent**; `extract_sections`
  must keep ending at any-level headers (stored `sections_json` depends on
  it). `resolve_range()` is shared by `doc_get_text` and `doc_summary`.
- **Failed extraction raises**: scanned PDFs name OCR as the remedy (only an
  *unimportable* backend means "install a library"); binary content raises
  instead of mojibake. `fetch_url_content` retries with backoff and enforces
  `MAX_DOCUMENT_BYTES` while streaming.
- Deliberate behaviour changes: non-UTF-8 text documents and image-only
  PDFs are now rejected rather than ingested as garbage.

**Testing lesson.** Several of these were found by *mutating the fix back*
and watching the suite stay green; two `caplog.at_level(logging.ERROR)`
assertions were vacuous because the code logs at WARNING.

**Known gaps:** no test for "some rows dimension-mismatched AND the rest
below threshold" in `doc_ask`; no summary regeneration path; no OCR;
no charset detection; `INDEXING_RETRY_INTERVAL` hardcoded at 60 s; panel
does not render `summary_status`/`fidelity` (**#81**); pre-#78
`'(Empty PDF)'` documents remain answerable (**#80**). Contracts are covered
by `tests/test_tools_document_failures.py`, `tests/test_background.py` and
`tests/test_tools_document*.py`.

## Lint gate (#86, PR #91 — keep in mind)

`tests/test_lint.py` fails on any `ruff --select F` finding in `consensus/`
(`tests/` is not linted). `kg_query` dropped its fake `path` mode and rejects
unknown modes; `tools_memory` handler failures log via `_tool_failure`
(`MemoryUnavailableError` → warning; `EmbeddingClient.embed` logs a
non-transport fault such as a malformed response with its traceback first).
**pytest was declared nowhere**, so `uv run pytest` had silently used pyenv's
global Python. The `dev` group (`pytest`, `pytest-asyncio` 1.x, `ruff`) fixes
that. pytest-asyncio 1.x leaves no current event loop, so write async tests
as `async def`, never `get_event_loop().run_until_complete`.

## Speaking rotation after start (#104 — keep this contract)

`app_discussion_rotation.update_rotation(discussion, db, add=, remove=)` is
the only code that changes the rotation of a started discussion;
`add_to_discussion`, `remove_from_discussion` and `set_participant_role` all
route through it (`reorder_da_in_turn_order` is gone). It keeps four copies in
step — `turn_order`, `base_turn_order`, `method_state` `_turn_order` /
`_turn_index`, and `discussion_members.turn_position` (the positions in one
transaction via `db.set_member_turn_positions`; the member row and
`method_state` are separate commits — #107). Rules: the
devil's advocate is last in both orders; a reorder never changes whose turn
it is (`carry_turn_index`; a departed holder passes the turn to the next
remaining member); the `_turn_index` stamp is re-pointed at the carried live
index, not dropped, so a reload still restores a #19 mid-round reset; a
joiner enters the current phase only when that phase runs the whole roster —
a narrowed phase (moderator-only, red team) is left alone until the method
next derives the order (normally the next transition); a removal that would
empty the current phase falls back to the roster, as
`apply_method_turn_order` does. Only a **live** (active/paused) discussion
persists any of this (`LIVE_STATUSES`): edits to a concluded discussion still
in memory come from the setup screen after a refresh and are input for the
next `start_discussion`, so they stay in memory and never rewrite the
concluded record. Removing a non-member is an error. Tests:
`tests/test_app_discussion_rotation.py` (each rule mutation-checked).

## Open work

### Issue #61 — modules over the ~500-line golden rule (in progress)

**Done — eight slices** (detail in git history; one PR each):

1. `app_discussion_flow.py` (1254) → `app_discussion_flow/`. **Watch
   `turns.py` (499)**: the next addition crosses the limit — split then.
2. `tools_document.py` (1277) → `tools_document/`. Its review's
   pre-existing defects became #78.
3. `app.py` (1284) → `app.py` (344 at the time; 346 now) + `app_api/` mixins, composed like
   `Database` composes `db/` (chosen with the repo owner: ~500 lines were
   already one-line delegators, so moving logic alone could not get it under
   the limit). **Moved code logs under `consensus.app_api.*`, not a child of
   `consensus.app`** — `caplog` on the old name sees nothing. Image-fetch
   retry is #88; URL redaction in fetch-failure logs is #89.
4. `tools_memory.py` (698) → `tools_memory/` (constants, embedding, schemas,
   handlers, handlers_kg, provider; largest 288). Coverage 45% → 100% first
   (`tests/test_tools_memory_{embedding,handlers}.py`); the move commit is
   byte-identical per range. The characterization pass found three defects,
   fixed in the PR's first commit: semantic-context lazy indexing ran only
   once per discussion per process (bare-id marker added, scoped key
   discarded — now one shared `ensure_discussion_indexed()`); the embed
   retry loop slept after its final attempt; `kg_assert`'s background embed
   stopped silently on an outage. Submodule loggers are children of
   `consensus.tools_memory`, so old `caplog` names still work.
5. `tools_python.py` (622) → `tools_python/` (constants, schemas, execution,
   install, provider; largest 295). Coverage 82% → 100% first
   (`tests/test_tools_python_failures.py`); facade exports only
   `create_python_provider`. Defects fixed: **`uv pip install` had no
   `--python`**, so it installed into whatever venv `VIRTUAL_ENV`/the cwd
   resolved to while the worker imports from `sys.executable` (now
   `--python sys.executable`); a timed-out install was left running; the
   already-installed check and the CPU-limit fallback failed silently;
   `_init_python_tools` logged load failures at INFO (now WARNING +
   traceback; the missing UI flag is **#96**). The PR review then found
   the cancellation gap: a cancelled turn left the sandbox worker and `uv`
   running and the approval wait swallowed the task's own cancellation
   (defeating outer `wait_for` caps) — all now kill/re-raise. Also: worker
   catches `SystemExit` so output survives, unclear approval replies are no
   longer reported as denials, silent failure paths log. The Seatbelt
   profile's write confinement is looser than documented — docs corrected,
   tightening is **#97**.
6. `tools_image.py` (643 after fixes) → `tools_image/` (constants, storage,
   fetch, vision, schemas, handlers, provider; largest 190). Coverage 38% →
   100% first (`tests/test_tools_image.py`; `tests/image_helpers.py`
   `patch_image_module` rebinds a name in every `consensus.tools_image*`
   module). **URL fetching is one policy in `consensus/http_fetch.py`**
   (`fetch_capped`: retry/backoff on transient faults, optional `deadline`,
   streaming cap, a `guard` on *every redirect hop*; `redact_url` for logs)
   shared by document, image and `fetch_webpage` fetches — closed #88/#89.
   `require_public_host` (SSRF guard) runs **in every mode**, desktop too
   (#98). Defects fixed: redirect-hop SSRF, failed vision calls persisted as
   descriptions, SVGs sent to vision models, GIF→JPEG resize, INFO-level load
   failures. **#99**'s CSP is live; the issue stays open for its route test.
   Lesson: every fetch test installs a fake transport — without one it passes
   while the guard works and hits the real network the moment it breaks.
7. `methods/base.py` (517 → 469) → `LINEAR_NEXT`, `Phase`,
   `ProcessedResponse`, `OutputToolSpec` moved verbatim to
   `methods/phase_types.py`. `base.py` **re-exports them** (`__all__`), so
   none of the 124 importing modules changed; guard is
   `tests/test_methods_base_facade.py`. Import new code from either.
8. `app_discussion_setup.py` (509 → 426) → participant-role code
   (`set_participant_role`, `auto_assign_da_tools`,
   `reorder_da_in_turn_order`, `_DA_TOOL_NAMES`) moved verbatim to
   `app_discussion_roles.py`, plus `revert_other_devils_advocates`, newly
   extracted from the demotion loop both callers shared (102, 100% covered by
   `tests/test_app_discussion_roles.py`; it was 0%). No re-export — callers
   import the defining module. Defect fixed: a devil's advocate added
   mid-discussion demoted the old one in memory only, so a reload restored
   two. Turn-order desync on mid-discussion roster/role changes was **#104**
   (fixed — contract below).

Smaller slices came out of the error-visibility work: `ai_response.py` (from
`ai_client.py`) and `static/turn-notices.js` (from `discussion-actions.js`).

**Facade guards.** A dropped re-export fails *late*: almost every consumer
imports lazily inside function bodies (exception: `moderator.py` imports
`is_vision_capable` / `build_image_content_blocks` at module level, so a
dropped one of those fails at app import), and `app._init_memory_tools` /
`_init_document_tools` / `_init_python_tools` / `_init_image_tools` turn an `ImportError` into "feature unavailable" (now
logged at WARNING; it was INFO before the tools_memory slice, so a broken facade quietly
disabled the feature). `tests/test_app_discussion_flow_facade.py`,
`test_tools_document_facade.py`, `test_tools_memory_facade.py`,
`test_tools_python_facade.py` and `test_tools_image_facade.py` pin
`__all__` by identity and AST-scan **every module under `consensus/`** for
call sites. Keep them in step when the public API changes.

**Still over the limit**, with statement coverage from the full suite
(`uv run --with pytest-cov pytest --cov=consensus`):

| Lines | Cover | File |
|------:|------:|------|
| 1229 | **0%** | `consensus/server.py` |
| 787 | 49% | `consensus/auth.py` |
| 775 | 59% | `consensus/moderator.py` (one `Moderator` class) |
| 714 | 51% | `consensus/mcp_server.py` |
| 676 | **0%** | `consensus/evaluation/runner.py` |
| 636 | 35% | `consensus/desktop.py` |
| 589 | **0%** | `consensus/evaluation/eval_db.py` |
| 529 | **0%** | `consensus/evaluation/scorer.py` |

Structural only — no behaviour change — one module per PR, suite green before
and after.

**Next-best target: `consensus/evaluation/scorer.py`** (529, 0%) — only
29 over, and it splits cleanly: pure scoring functions (`normalise`,
`score_diagnostic_accuracy`, `score_differential_quality`, `score_run`)
plus a ~200-line CLI report (`print_report`, `main`) that can move to its
own module. Characterize the pure functions first (cheap — no I/O).
`moderator.py` and `auth.py` are bigger payoffs but are a single class and a
security-critical module at ~50–60% — build their nets before moving a line.

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
   the characterization tests were written against the old shape. On the
   `tools_document` slice three such mutations survived the whole suite at 100% coverage.
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
copying for the next one. Drive handlers through the entry point production
uses (e.g. `create_memory_provider(db).execute(name, args, ctx)`) rather than
importing private handler functions, so the tests need no edits when the
split moves them. **Grep for duplicated logic while writing the net** — two
copies of the same bookkeeping that have drifted apart (as the two
lazy-indexing sites in `tools_memory` and `context_strategies` had) are the
likeliest pre-existing bug.

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
  (`scripts/release_pypi.sh` now cleans `build/` and `*.egg-info/` and
  `check_no_shadowed_packages` rejects a wheel shipping both `X.py` and `X/` —
  a stale `build/lib` once packaged a moved module beside its replacement.)

### Open GitHub issues (besides #61)

#102 follow-ups from the PR #100 review (Retry-After, `doc_add` ingest
budget, DNS-rebinding pin, `_init_image_tools` catch, test file size); #99 route test for the image-serve CSP (fix landed in PR #100; needs a
`server.py` route-test harness); #97 Seatbelt profile does not confine writes; #96 no UI flag when
Python or image tools fail to load; #92 tool error results echo raw
exception text to the model and UI; #81 persist parse fidelity/notes; #80
pre-#78 `'(Empty PDF)'` documents. (#88/#89/#98 closed with PR #100.)

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
