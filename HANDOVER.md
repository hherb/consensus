# HANDOVER

_Last updated: 2026-11-15. `main` is at **v2.0.0** (released 2026-07-20) plus
the merged #61 slices through `auth.py` → `auth/` (PR #129), the #121 fix
(MCP tool failures are `isError` results, PR #122), the #104 rotation fix and
the #115/#118 pricing fixes (PRs #117/#119). The sixteenth and **last** #61
slice — `server.py` → `server/`, with a route-test net and seven web-mode
defect fixes — is on `refactor/split-server-61` (suite at **4332 passing**);
once it merges, every Python module is under the ~500-line golden rule and
#61 is closed. The discussion-method review & repair campaign (#12–#48,
#56–#60), alpha/stable distribution (PyPI `consensus-app` + notarized macOS
DMG), the public website, flow-error visibility (#71–#74), tools_document
failure visibility (#78) and the lint gate (#86, `tests/test_lint.py`) are
all merged — contracts summarised below._

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
| Tests | 4332 passing (`uv run pytest`, ~60 s, offline; runs in `.venv` via the `dev` group) |
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
- **No `oauth` in names that reach a log call** (`SIGN_IN_PROVIDERS`,
  `SIGN_IN_MAX_RETRIES`, …): CodeQL's password heuristic matches `oauth` and
  raised high-severity clear-text-logging alerts that block merging (PR #129).
  Check with `gh codeql database analyze` on the
  `Security/CWE-312/CleartextLogging.ql` query.
- `create_token` / `store_oauth_state` prune expired rows (nothing else
  does); `login` checks the password before saying "disabled". Never change
  `PASSWORD_HASH_ITERATIONS` before **#127** lands.

Tests: `tests/test_auth_db.py`, `_manager.py`, `_oauth_exchange.py`,
`_oauth.py` (100% coverage; providers faked at the httpx transport via
`tests/auth_helpers.install_routes`, which scripts retries with a list), and
the guard `tests/test_auth_facade.py`. Patch through the defining module
(`auth_helpers.module_of`); `fast_hashing` lowers PBKDF2 rounds.

## Web server (`server/`, #61 slice 16 — keep these contracts)

`create_web_app(host, port, multi_user)` builds the aiohttp app;
`launch_web` serves it. All per-server state is one `ServerContext` on the
app under `CTX_KEY`; handlers are module-level and read
`request.app[CTX_KEY]`. The net (`tests/test_server_*.py`, fixtures in
`tests/server_helpers.py`, which point `HOME` at a temp dir) drives the real
middleware through aiohttp's `TestClient`; it found the defects below.

- **A POST to `/api/` or `/auth/` must be JSON or carry `X-Consensus-Request`**
  (`CSRF_HEADER`). Multipart uploads send the header (`UPLOAD_HEADERS` in
  `static/api.js`); everything else sends JSON. Before this, uploads, logout
  (the token was never revoked; a reload signed the user back in) and the
  embedding test all got 415. `test_frontend_posts_pass_the_csrf_guard`
  scans `static/*.js` — a new bare `fetch` POST fails it.
- **Uploads read through `request_io.read_upload`**, which lifts aiohttp's
  1 MiB default body limit to `MAX_DOCUMENT_BYTES` / `MAX_IMAGE_BYTES` for
  that read only (everything else stays at 1 MiB). Those constants are
  imported *inside* the handlers, like `app._init_*_tools`, so a broken
  optional backend cannot stop the server from starting.
- **`auth_middleware` resolves the user for `/auth/me`** too (enforces only
  on `/api/*`); before, both `/auth/me` routes were always 401.
- Bodies go through `read_json_object` (non-object → 400); malformed ids and
  `discussion_id`s are 400s, as are non-string credentials and profile
  fields (`routes_auth.non_string_field`). `session_app()` is the one
  401/503 preamble for the file routes; `CAPACITY_ERROR` the one capacity
  message.
- **The frontend opens the SSE stream after the first `get_state`**
  (`static/app.js`), so a first `--multi-user` visit's stream joins the
  session that call created instead of an orphan one (verified by reading
  only — no JS harness).
- `SessionManager` expiry and `stop()` share `_close_app()` (expiry used to
  skip `app.shutdown()`, leaking MCP subprocesses); session ids in logs are
  truncated. `login_attempts` / `rate_limits` are pruned by `prune_stale()`.
- Tests patch a setting where it is read (`middleware.RATE_LIMIT_MAX`,
  `routes_auth.LOGIN_RATE_LIMIT_*`, `routes_misc.SSE_KEEPALIVE_SECONDS`).
  `tests/test_server_api.py` `DISPATCH` pins every `/api/<method>` entry's
  forwarded arguments; `tests/test_server_facade.py` pins `__all__`, the one
  consumer (`__main__.py`) and every route → handler. **A new route goes in
  `app_factory.py` and that table; a new dispatch entry in `DISPATCH`.**

## Open work

### Issue #61 — modules over the ~500-line golden rule (done on this branch)

Sixteen slices, one PR each (detail in git history and the contract sections
above): `app_discussion_flow/`, `tools_document/`, `app.py` → `app_api/`
mixins (**moved code logs under `consensus.app_api.*`**), `tools_memory/`,
`tools_python/`, `tools_image/` (+ `http_fetch.py`), `methods/base.py` →
`phase_types.py`, `app_discussion_setup.py` → `app_discussion_roles.py`,
`evaluation/` (`report.py`, eval_db mixins, `runner_db.py` /
`runner_common.py`), `mcp_server/`, `desktop/`, `moderator/`, `auth/`,
`server/`. Eval contracts worth keeping: a failed LLM judgement returns 502
and never overwrites an earlier score; `/eval/` is **not mounted in
`--multi-user`**; a run with a failed turn/summary/synthesis is `error`, not
`done`. `mcp_server` contracts: a failed tool call is an `isError` result
(#121); `run_discussion` reports `turn_errors`, `error`, `stop_reason`. A new
desktop bridge method goes in a mixin **and** in `BRIDGE_API`.

**Watch:** `app_discussion_flow/turns.py` (499) and `pricing.py` (498) — the
next addition crosses the limit, so split first. `evaluation/static/eval.js`
(645) is pre-existing JS over the limit.

**Facade guards.** Each re-exporting package has a
`tests/test_<name>_facade.py` that pins `__all__` by identity and AST-scans
`consensus/` for call sites (consumers import lazily, so a dropped re-export
fails only in production). Keep them in step with the public API.

**Recipe for the next split** (worked for all sixteen):
0. Check coverage first (`uv run --with pytest-cov pytest -q
   --cov=consensus.<module> --cov-report=term-missing`); a green suite over
   an untested module proves nothing — write the characterization net and
   commit it *before* a line moves. Fix each defect it finds in its own
   commit. Grep for duplicated logic while writing it: drifted copies were
   the likeliest pre-existing bug every time. Use a real `Database`
   (`tmp_db`), fakes only at the network/GUI edges, shared fakes in a
   `tests/*_helpers.py`, and drive the entry point production uses.
1. Move by line range with a throwaway script that checks every moved line
   reappears once (`server.py` also rewrote closure names to `ctx.<name>`).
2. Fix relative-import depth, **including imports inside function bodies**,
   and `__file__`-relative paths.
3. Re-export from `__init__.py`; point test patches at the submodule that
   reads the name.
4. `uvx ruff check --select F <pkg>`, then the full suite.
5. Add the facade guard; delete a re-export and watch it fail.
6. **Mutate the new wiring and watch it fail** — coverage measures lines
   executed, not behaviour constrained (on `server/` a second context and the
   cookie max-age survived until tests were added).
7. Update every doc naming the old file *and* the content beside the name.

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

### Open GitHub issues

#132 non-dispatch routes create a `--multi-user` session without setting its
cookie (orphaned sessions hold capacity); #131 login timing reveals which
emails have a password account; #130 an OAuth callback has no overall
deadline; #128 unverified OAuth sign-up can pre-hijack an address; #127 stored
password hashes lack their PBKDF2 iteration count; #125 pywebview exposes all
of `ConsensusApp` to JS; #124 validate MCP tool arguments against
`inputSchema`; #107 a rotation change spans several commits (crash mid-update
desyncs members and `method_state`); #102 follow-ups from the PR #100 review
(Retry-After, `doc_add` ingest budget, DNS-rebinding pin, `_init_image_tools`
catch, test file size); #97 Seatbelt profile does not confine writes; #96 no
UI flag when Python or image tools fail to load; #92 tool error results echo
raw exception text to the model and UI (the `/api/<method>` 500 body does too
— noted on the issue); #81 persist parse fidelity/notes; #80 pre-#78
`'(Empty PDF)'` documents. #99 (image-serve CSP route test) is closed by this
branch.

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
