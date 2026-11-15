# HANDOVER

_Last updated: 2026-11-15. `main` is at **v2.0.0** (released 2026-07-20) plus
the merged #61 slices (through `desktop.py` → `desktop/`, PR #123), the #121
fix (MCP tool failures are `isError` results, PR #122), the #104 rotation fix,
the #115/#118 pricing fixes (PRs #117/#119) and `moderator.py` → `moderator/`
(PR #126). The fifteenth #61 slice (`auth.py` → `auth/`, plus eight auth
defect fixes and the PR #129 review fixes) is on `refactor/split-auth-61` (PR
#129, suite at **4040 passing**). The discussion-method review & repair
campaign (#12–#48, #56–#60) is finished and merged; so is alpha/stable
distribution (PyPI `consensus-app` + notarized macOS DMG), the public website,
the flow-error-visibility work (#71–#74, PR #76), and the tools_document
failure-visibility work (#78) — contracts for both summarised below. The one
open structural issue is **#61** (modules over the ~500-line golden rule):
`app_discussion_flow`, `tools_document`, `app.py` (→ `app_api/` mixins),
`tools_memory`, `tools_python`, `tools_image`, `methods/base.py`,
`app_discussion_setup.py`, `evaluation/scorer.py`, `evaluation/eval_db.py`,
`evaluation/runner.py`, `mcp_server.py`, `desktop.py`, `moderator.py` and
`auth.py` are split; only `server.py` remains over the limit. **#86** (lint
gate) is done: zero `ruff --select F` findings in `consensus/`, enforced by
`tests/test_lint.py`._

This file briefs the next session on what is done, what is still open, and
the conventions to keep; delete sections that are no longer instructive.
Per-PR detail lives in git history and `docs/superpowers/` — do not
re-narrate it here.

## Where the project stands

| Area | State |
|------|-------|
| Discussion methods | 18 methods, all reviewed/repaired; composable `PhaseHandler` library (68 handlers) |
| Structured outputs | Forced tool calls for every structured phase; humans get a schema-driven form (#57) |
| Distribution | `consensus-app` on PyPI; notarized + stapled macOS DMG; v2.0.0 is the current stable |
| Website | `website/` — static site on Cloudflare Pages at https://consensus-ai.org/, incl. a discussion-methods catalogue (`methods.html`, PRs #82/#83; `_headers` cache-busts CSS) |
| Tests | 4040 passing (`uv run pytest`, ~45–60 s, offline; runs in `.venv` via the `dev` group) |
| Docs | README, QUICKSTART, user manual and `docs/devel/` aligned with the code (PR #62) |

### Merged campaigns (detail in git history)

Method review & repair (#12–#30, #42, #48, #56); new methods NGT (#24), MCDA
(#25), Double Crux (#27), Tree of Thoughts (#26); evidence-tracked phases
(#28, soft by design — see `memory/evidence-gating-philosophy.md`);
structured-phase human input (#57, #59, #60); method-flow E2E tests
(`tests/test_method_flow_e2e.py`); provider resilience (PR #64, #65 — see
ROADMAP); distribution (runbook in
`memory/alpha-distribution-release-process.md`); flow and document failure
visibility (#71–#74, #78) and the lint gate (#86) — contracts below.

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
- **`helpers.post_notice` is how a notice reaches the transcript.** It
  appends the in-memory `Message` first and guards `db.add_message` (a failed
  DB otherwise raised a second exception and the user saw nothing); it
  returns `(message, persisted)`, appends `UNSAVED_NOTICE_SUFFIX` on a failed
  write, and callers surface `notice_unsaved`.
- **`conclude_discussion` returns `conclusion_error`** and posts a notice;
  the discussion still concludes. `synthesis_shown` picks
  `_CONCLUSION_NOT_SAVED_NOTICE` over `_CONCLUSION_FAILURE_NOTICE`. `mediate`
  posts an equivalent notice.
- **A failed Triage recommendation is never presented as one.**
  `MethodRecommender.recommend` **raises `RecommenderError`** and callers own
  the fallback; both failure paths in `run_triage_recommender` go through
  `_record_recommender_failure`, with key resolution and client construction
  inside the `try` and a guarded `finally` close.
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

**Testing lesson.** #72 stayed green because tests mocked the very method
under test; mock one layer further out (the `AIClient`). Tests:
`tests/test_flow_error_*.py`, `_triage_recommender.py`,
`_moderator_actions.py`.

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
(`tests/` is not linted). The `dev` group pins `pytest`, `pytest-asyncio`
1.x (`asyncio_mode = auto`; no current event loop, so write async tests as
`async def`) and `ruff`.

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

## Pricing downloads (#115, #118 — keep this contract)

`PricingCache` lookups (`calculate_cost_with_refresh`, `has_input_modality`,
`get_context_length`, `supports_tools`) are reached from the event loop.
`SessionManager` also builds a `ConsensusApp`, with an empty pricing table,
inside a request handler for every new `--multi-user` visitor. So **no lookup
downloads inline.** Startup with a stale cache or one with no capability data
(empty, or from before migration 014), and a lookup of an unknown model, call
`refresh_in_background()`, which runs at most one download thread per cache
(per `ConsensusApp`). The lookup that triggered it returns `None`; later
lookups see the data. A model counts as tried only once a download starts for
it. `refresh()` goes through `http_fetch.fetch_capped` (retry/backoff) inside
`asyncio.run` in that thread; it returns `False` on a fetch failure, an
unreadable or empty catalogue, or a DB failure; malformed entries are skipped.

**The tool-capability gate waits for the download in flight** (#118):
`ConsensusApp.start_discussion` (**async**; desktop wraps it in
`_run_async`) and `recommend_method` first `await
pricing.wait_for_refresh()` (at most `CAPABILITY_WAIT_SECONDS`, 15 s),
for starts the gate applies to (`method_needs_tools`) plus Triage. An
unknown model in a populated cache still passes on its first setup. Tests:
`tests/test_pricing_background.py`, `_capability_wait.py` (**join the
download thread before closing the DB** — otherwise it segfaults).

## Authentication (`auth/`, #61 slice 15 — keep these contracts)

The characterization net found eight defects, each fixed in its own commit
before the move. Keep these when touching `consensus/auth/`:

- **Email is never user-editable.** `update_user` allows only
  `display_name`/`avatar_url`. Nothing verifies a new address, and OAuth
  auto-links a provider-verified email to the account holding it, so an
  edit (or any future "change email" feature without verification) is an
  account-takeover path.
- **A verified OAuth link to a password account clears the password and
  revokes every session**, in the same transaction as the link
  (`link_oauth(..., lock_out_password=True)`). Registration never verifies
  the email, so the password may belong to a squatter. Repo owner's
  decision (2026-11-15). An unverified provider email never links; an
  unverified OAuth *sign-up* can still squat an address (**#128**).
- **An OAuth reply without an email *or* provider user id is refused** —
  an empty id would match another empty-id account via the legacy columns.
  Ids go through `oauth._provider_id`, so a JSON `null` id stays empty
  rather than becoming the shared string `"None"`.
- **`link_oauth` preserves the legacy-column identity** in
  `user_oauth_identities` before overwriting it, in one transaction.
- **Provider requests go through `oauth._send_with_retry`** (shared
  `http_fetch` retry policy, golden rule 5). A GitHub email-lookup failure is
  logged, means "unverified" and sets `email_lookup_failed`, so the user is
  told to retry rather than to use another sign-in method.
- Providers are offered only with both client ID and secret set.
- `create_token` / `store_oauth_state` prune expired rows (nothing else
  does); `login` checks the password before saying "disabled". Never change
  `PASSWORD_HASH_ITERATIONS` before **#127** lands.

Tests: `tests/test_auth_db.py`, `_manager.py`, `_oauth_exchange.py`,
`_oauth.py` (100% coverage; providers faked at the httpx transport via
`tests/auth_helpers.install_routes`, which scripts retries with a list), and
the guard `tests/test_auth_facade.py`. Patch through the defining module
(`auth_helpers.module_of`); `fast_hashing` lowers PBKDF2 rounds.

## Open work

### Issue #61 — modules over the ~500-line golden rule (in progress)

**Done — fifteen slices** (detail in git history; one PR each):

1. `app_discussion_flow.py` → `app_discussion_flow/`. **Watch `turns.py`
   (499)**: the next addition crosses the limit — split then.
2–8. `tools_document/` (defects → #78); `app.py` → `app_api/` mixins
   (**moved code logs under `consensus.app_api.*`** — `caplog` on
   `consensus.app` sees nothing); `tools_memory/` (one shared
   `ensure_discussion_indexed()`); `tools_python/` (facade exports only
   `create_python_provider`); `tools_image/` (**URL fetching is one policy in
   `http_fetch.py`**; every fetch test installs a fake transport);
   `methods/base.py` → `phase_types.py` (re-exported, guarded by
   `tests/test_methods_base_facade.py`); `app_discussion_setup.py` → role
   code in `app_discussion_roles.py` (no re-export).
9–11. `evaluation/`: `scorer.py` → `report.py`; `eval_db.py` →
   `CatalogMixin`/`RunsMixin`/`SeedingMixin` siblings (not a package:
   `_get_migrations_dir` resolves from `__file__`); `runner.py` → web path in
   `runner_db.py`, shared `runner_common.py` (PR #116). Eval contracts: a
   failed LLM judgement returns 502 and **never overwrites an earlier score**;
   `/eval/api/` writes must be `application/json` (415, CSRF) with an object
   body (400), field types checked in `request_checks.py` (400), known
   constraint conflicts → 409; **`/eval/` is not mounted in `--multi-user`**;
   the judge's BYOK key stays in the input field, never `sessionStorage`;
   every eval-DB write runs in `with self._lock, self.conn`. A run with a
   failed turn/summary/synthesis, or no AI speaker due, is `error`, not
   `done`; the BYOK ContextVar is **set even when empty**; a scoring failure
   keeps the run `done` with `AUTO_SCORE_FAILED_PREFIX` (#114); unstarted runs
   are closed on cancel/shutdown/crash (#113); `MAX_NUM_ROUNDS = 20` (mirrored
   in `eval.html`), `MAX_BATCH_RUNS = 1000` (#112). `JUDGE_DIMENSIONS` is
   mirrored in `eval_scoring.js` and the judge prompt (drift-tested);
   `evaluation/static/eval.js` (645) is pre-existing over the limit.
12. `mcp_server.py` (714) → `mcp_server/` mixins (`server.py`,
   `tool_defs.py`, `handlers_read.py`, `handlers_write.py`, `constants.py`,
   `errors.py`, `__main__.py`); net `tests/test_mcp_server_handlers.py` +
   `_robustness.py` (fixtures in `tests/mcp_server_helpers.py`), guard
   `tests/test_mcp_server_facade.py`. Contracts: **a failed tool call is an
   `isError` result** — handlers raise `ToolError` (WARNING, no traceback),
   never return `"Error: ..."` text (#121); a wrong-typed argument still
   takes the bug path (#124). `run_discussion` reports `turn_errors`,
   `error` (why it stopped early — a failed `complete_turn` stops the loop),
   `stop_reason` and whole-round `rounds_completed`; `moderator_id` must be
   in `entity_ids`. Malformed stdio input gets a JSON-RPC error reply, a
   dispatch bug `-32603`, a notification never a reply. The temporary
   `ConsensusApp` is `shutdown()` per call; its DB connection is left to GC
   (closing it under the pricing thread segfaults — join that first).
13. `desktop.py` (636) → `desktop/`: `bridge.py` (`DesktopBridge`) composing
   `catalog.py` / `discussion.py` / `files.py` mixins, `launcher.py`
   (resolves `pkg_dir` one level up), `constants.py`.
   `tests/test_desktop_bridge_api.py` pins `BRIDGE_API` against every
   `pywebview.api.X` call in `static/` (pywebview also exposes `api.app.*`,
   #125); `tests/test_desktop_bridge.py` uses a `FakeWindow`
   (`tests/desktop_helpers.py`) and a fake `webview`; guard
   `tests/test_desktop_facade.py`. **A new bridge method goes in a mixin and
   in `BRIDGE_API`.**
14. `moderator.py` (775) → `moderator/` mixins (`core`, `context`, `turn`,
   `synthesis`, `constants`). Net `tests/test_moderator_turn.py` /
   `_synthesis.py` driven by `ScriptedLLM` (`tests/moderator_helpers.py`);
   `use_method` patches `get_active_method` in every mixin module. Guard
   `tests/test_moderator_facade.py`.
15. `auth.py` (787, 49% covered) → `auth/` (`constants`, `models`,
   `passwords`, `database`, `oauth`, `manager`); see the Authentication
   section above.

**Facade guards.** A dropped re-export fails *late*: consumers mostly import
lazily inside function bodies, and the `app._init_*_tools` methods turn an
`ImportError` into "feature unavailable" (logged at WARNING). Each
re-exporting facade has a `tests/test_<name>_facade.py` that pins `__all__`
by identity and AST-scans every module under `consensus/` for call sites.
Keep them in step when the public API changes.

**Still over the limit**, with statement coverage from the full suite
(`uv run --with pytest-cov pytest --cov=consensus`):

| Lines | Cover | File |
|------:|------:|------|
| 1241 | **0%** | `consensus/server.py` |

Structural only — no behaviour change — one module per PR, suite green before
and after.

**Next target: `server.py`, the last one — read this before picking it.** It
is a single 1170-line `launch_web()` whose middleware and ~35 handlers are all
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
1. Move code by line range so it transfers verbatim, with a throwaway
   script that assembles the new files from `(start, end)` ranges and checks
   each range back against `git show HEAD:<file>` (known edits undone) plus
   that every non-blank original line is placed once — one pass did it for
   `mcp_server` and `desktop`.
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
   `app.py` misses the next consumer someone adds. Name consumers by path
   relative to `consensus/`, not bare filename — the split put a second
   `server.py` under `consensus/`.
6. **Mutate the new wiring and watch it fail.** The glue a split *writes* —
   the factory that forwards dependencies to handlers, the guard conditions
   duplicated across two call sites, the keyword names production passes — is
   the code most likely to be got wrong and least likely to be covered, since
   the characterization tests were written against the old shape. On the
   `tools_document` slice three such mutations survived the whole suite at 100% coverage.
   Coverage measures lines executed, not behaviour constrained.
7. Update the docs and comments that name the old module file
   (`grep -rn "<name>\.py" --include='*.md' --include='*.py'`: `CLAUDE.md`,
   `README.md`, `docs/devel/*`, test docstrings,
   `docs/BUILTIN_TOOLS.md`). Check the *content* beside the name too — the
   `doc_*` tables and the desktop bridge method list were both wrong in
   blocks earlier PRs had edited only for the module name.

**Writing the safety net, if the target is untested.** Real `Database`
(`tmp_db`), fakes only at the network/GUI edges, shared fakes in a
`tests/*_helpers.py` module. `tests/document_helpers.py`
`patch_where_defined(monkeypatch, anchor, name, replacement)` patches through
`sys.modules[anchor.__module__]`, so tests survive the move. Drive code
through the entry point production uses, not private functions. **Grep for
duplicated logic while writing the net** — drifted copies of the same
bookkeeping are the likeliest pre-existing bug.

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
- **Packaging** — `packaging/macos/make_icns.sh`
  regeneration needs Pillow on system python3; icon bubbles blur at 16–32 px.
  `check_no_shadowed_packages` (`scripts/release_pypi.sh`) rejects a wheel
  shipping both `X.py` and `X/`.

### Open GitHub issues (besides #61)

#131 login timing reveals which emails have a password account; #130 an OAuth
callback has no overall deadline; #128 unverified OAuth sign-up can pre-hijack
an address; #127 stored password hashes lack their PBKDF2 iteration count;
#107 a rotation change spans several commits (crash mid-update desyncs members
and `method_state`); #102 follow-ups from the PR #100 review (Retry-After,
`doc_add` ingest budget, DNS-rebinding pin, `_init_image_tools` catch, test
file size); #99 route test for the image-serve CSP (fix landed in PR #100;
needs a `server.py` route-test harness); #97 Seatbelt profile does not confine
writes; #96 no UI flag when Python or image tools fail to load; #92 tool error
results echo raw exception text to the model and UI; #81 persist parse
fidelity/notes; #80 pre-#78 `'(Empty PDF)'` documents.

### Roadmap

`ROADMAP.md`: nearest ⬜ items are Argument Mapping, Tournament /
Superforecasting, token-aware context windowing, lazy message loading.

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
- **The suite is offline.** `tests/conftest.py`'s autouse `offline_pricing`
  stubs `PricingCache.refresh`; a test of the refresh itself patches the
  instance. Fake LLMs at `AIClient._post_with_retry`
  (`tests/eval_runner_helpers.py`) to run a real `ConsensusApp` end to end.
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
