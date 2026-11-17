# HANDOVER

_Last updated: 2026-11-17. `main` is at **v2.0.0** (released 2026-07-20) plus
everything merged since, including the whole of #61 (every Python module is
under the ~500-line golden rule), the auth hardening of PRs #140 (#127,
#131), #142 (#141, #130) and #143 (#138), and PR #144, which stops pywebview
letting page script reach `ConsensusApp` and its `Database` through the
desktop bridge (#125). This branch, `fix/mcp-arg-validation-124`, checks
`consensus-mcp` tool arguments against each tool's `inputSchema` (#124). The
suite is at **4498 passing**.
Merged campaigns and their contracts are summarised below._

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
| Tests | 4498 passing (`uv run pytest`, ~60 s, offline; runs in `.venv` via the `dev` group) |
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

**Testing lesson.** Mock one layer out (the `AIClient`), never the method
under test (#72 stayed green that way). Tests: `tests/test_flow_error_*.py`.

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

**Testing lesson.** Mutate the fix back and watch a test fail; two
`caplog.at_level(logging.ERROR)` checks were vacuous (the code logs at WARNING).

**Known gaps:** no test for "some rows dimension-mismatched AND the rest
below threshold" in `doc_ask`; no summary regeneration path; no OCR;
no charset detection; `INDEXING_RETRY_INTERVAL` hardcoded at 60 s; panel
does not render `summary_status`/`fidelity` (**#81**); pre-#78
`'(Empty PDF)'` documents remain answerable (**#80**). Contracts are covered
by `tests/test_tools_document_failures.py`, `tests/test_background.py` and
`tests/test_tools_document*.py`.

## Lint gate (#86 — keep in mind)

`tests/test_lint.py` fails on any `ruff --select F` finding in `consensus/`
(not `tests/`). `pytest-asyncio` 1.x runs in `asyncio_mode = auto`: write
async tests as `async def`.

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
  does); `login` checks the password before saying "disabled".
- **Password hashes record their cost** (#127):
  `pbkdf2_sha256$<iterations>$<salt>$<hash>`; `verify_password` uses the
  stored count, and the legacy `salt$hash` means
  `LEGACY_PASSWORD_HASH_ITERATIONS` (600k, never change it). After a
  successful login of an active user, `login` re-hashes when `needs_rehash`
  says so, so raising `PASSWORD_HASH_ITERATIONS` is now safe. The re-hash is
  a compare-and-swap (`change_password(..., expected=)`), so it cannot undo
  a concurrent OAuth lock-out, and a failed write is logged, never a failed
  login. An unreadable stored hash fails with a warning. An older build
  cannot read the new format (downgrade locks password users out).
- **Unknown emails cost a wrong password** (#131): no user or no password
  → `burn_password_check`, exactly one current-cost PBKDF2 against an
  all-zero dummy hash. Until a raised count has reached every user, a
  dormant account's old cheaper hash is still distinguishable. Registration
  still reveals a taken email (**#139**, needs email verification first).
- **Login and registration hash off the event loop** (#141):
  `routes_auth._hash_off_loop` runs `AuthManager.login` / `register` via
  `asyncio.to_thread`, at most `PASSWORD_HASH_CONCURRENCY` at once
  (`ServerContext.password_slots`), so a burst cannot take the default
  executor that DNS lookups share. Because the rate-limit check and the
  result are now split by an await, **the handler counts an attempt before
  hashing** and removes it on success. `AuthDatabase` reads go through
  `_query_one` under the write lock, so a worker-thread read never lands
  inside `link_oauth`'s transaction on the shared connection. Login issues
  its token with `create_token(password_hash=...)`, one statement that
  requires the verified hash and `is_active`, so an OAuth lock-out or a
  disable during the verify leaves no session; the rehash runs after it.
- **An OAuth exchange has a deadline** (#130): `oauth_callback` wraps
  `exchange_oauth_code` in `asyncio.timeout(SIGN_IN_DEADLINE_SECONDS)` (45 s,
  under nginx's default 60 s `proxy_read_timeout`). On expiry it logs and
  raises "did not answer in time" before any user or token is written.

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
middleware through aiohttp's `TestClient` (sessions and SSE in
`tests/test_server_sessions.py`); it found the defects below.

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
- Bodies go through `read_json_object` (non-object → 400, `/api/<method>`
  included; an oversized body stays a 413); malformed ids and
  `discussion_id`s are 400s, as are non-string credentials, profile fields
  and add-url titles (`request_io.non_string_field`). `CAPACITY_ERROR` is the
  one capacity message.
- **`/api/<method>` entries build a `functools.partial`; they never call**
  (#134). `handle_api` binds it against the method's signature before
  running it, so a missing key, an unconvertible or overflowing number, or a
  kwarg a fixed-signature method does not take (`**kwargs` methods such as
  `update_provider` ignore unknown keys) is a 400 `Invalid arguments for
  <method>: …`, while the same exception types raised *inside* the app method
  stay a logged 500. A new entry must follow the `lambda: partial(app.x, …)`
  shape. `DISPATCH` cases are bound against the real method signatures.
  Every JSON body number (here and in the add-url routes) goes through
  `request_io.int_field` / `float_field`, never bare `int()` / `float()`, so
  the 400 names the field and its type (`max_rounds must be an integer`).
- **The desktop bridge is never pywebview's `js_api`** (#125).
  `launch_desktop` registers `exposed_functions(bridge)` (its public bound
  methods) with `window.expose`. pywebview resolves a `js_api` call by
  walking the dotted name with plain `getattr`, private names and dunders
  included, so with the bridge as `js_api` page script could call
  `_app.db.<anything>` or `get_state.__self__._app` (a leading underscore and
  `_serializable = False` only hide a name from the generated
  `pywebview.api` object). Exposed functions are found by exact name.
  `TestDispatch` in `tests/test_desktop_bridge_api.py` drives pywebview's
  real `js_bridge_call` to prove the private paths do not resolve.
- **Only `POST /api/<method>` creates a `--multi-user` session** (#132,
  repo owner's choice). It sets the cookie on every reply that has a session,
  errors included (the capacity 503 has none). Every other `/api/` route goes through `session_app()`, which
  uses `SessionManager.lookup()` (never creates) and answers 401 or 400
  `NO_SESSION_ERROR`.
- **The SSE stream ends once its session is evicted** (#135): each keepalive
  tick checks `lookup(sid, touch=False) is app_instance`. `touch=False`
  matters: an open tab alone must not keep a session alive. The stream sends
  `session_expired` (the page shows a notice) before it ends. If a dispatch
  call has already re-created the session, the reconnect joins it; otherwise
  it gets the 400 and closes, and `static/app.js` reacts to `CLOSED` by
  calling `get_state` (which creates the next session) and reopening, with
  exponential backoff from `SSE_REOPEN_DELAY_MS` and a notice after
  `SSE_REOPEN_MAX_ATTEMPTS` (verified by reading only; there is no JS
  harness). An in-page sign-in reloads the page, so `init()` and the stream
  run for the signed-in user.
- **`run_migrations` re-checks the file**: its per-path guard skips only when
  the `migrations` table is still there. Eviction deletes a session's DB and
  a returning cookie re-creates it at the same path; it used to come back
  schema-less (`no such table: prompts` on every call).
- **PUT/DELETE carry no CSRF check by design**: a cross-site form cannot send
  them, and a cross-origin fetch needs a preflight the server never grants
  (`test_preflight_is_never_granted` pins the other half).
- `SessionManager` expiry and `stop()` share `_close_app()` (expiry used to
  skip `app.shutdown()`, leaking MCP subprocesses). Expiry deletes the DB
  under the lock but closes apps after releasing it (`_close_apps`): an MCP
  shutdown can take seconds. Session ids in logs are truncated.
  `login_attempts` / `rate_limits` are pruned by `prune_stale()`.
- Tests patch a setting where it is read (`middleware.RATE_LIMIT_MAX`,
  `routes_auth.LOGIN_RATE_LIMIT_*`, `routes_misc.SSE_KEEPALIVE_SECONDS`).
  `tests/test_server_api.py` `DISPATCH` pins every `/api/<method>` entry's
  forwarded arguments and must name exactly the table's keys (read by AST);
  `tests/test_server_facade.py` pins `__all__`, the one consumer
  (`__main__.py`) and every route → handler. **A new route goes in
  `app_factory.py` and that table; a new dispatch entry in `DISPATCH`.**

## Open work

### File size (#61 done, keep it that way)

All sixteen oversized Python modules are split, one PR each. Contracts worth
keeping: eval — a failed LLM judgement returns 502 and never overwrites an
earlier score; `/eval/` is **not mounted in `--multi-user`**; a run with a
failed turn/summary/synthesis is `error`, not `done`. `mcp_server` — a failed
tool call is an `isError` result (#121); `run_discussion` reports
`turn_errors`, `error`, `stop_reason`; `validation.validate_arguments` checks
every call against the tool's `inputSchema` before the handler runs and coerces
numeric strings (`"5"`, repo owner's call, #124), so a new tool definition may
use only its `SUPPORTED_KEYWORDS`. A new desktop bridge method goes in a mixin
**and** in `BRIDGE_API`; moved code logs under its new module.

**Watch:** `app_discussion_flow/turns.py` (499) and `pricing.py` (498): split
before the next addition. `evaluation/static/eval.js` (645) is pre-existing JS.

**Facade guards.** Each re-exporting package's `tests/test_<name>_facade.py`
pins `__all__` by identity and AST-scans `consensus/` for call sites (a
dropped lazy re-export fails only in production). Keep them in step.

**Recipe for a split** (worked for all sixteen):
0. Check coverage first (`uv run --with pytest-cov pytest -q
   --cov=consensus.<module> --cov-report=term-missing`); a green suite over
   an untested module proves nothing — write the characterization net and
   commit it *before* a line moves, and fix each defect it finds in its own
   commit. Drifted duplicate logic was the likeliest pre-existing bug every
   time. Use a real `Database` (`tmp_db`), fakes only at the network/GUI
   edges, and drive the entry point production uses.
1. Move by line range with a throwaway script that checks every moved line
   reappears once.
2. Fix relative-import depth, **including imports inside function bodies**,
   and `__file__`-relative paths.
3. Re-export from `__init__.py`; point test patches at the submodule that
   reads the name.
4. `uvx ruff check --select F <pkg>`, then the full suite.
5. Add the facade guard; delete a re-export and watch it fail.
6. **Mutate the new wiring and watch it fail** — coverage measures lines
   executed, not behaviour constrained.
7. Update every doc naming the old file *and* the content beside the name.

### Deferred follow-ups (no issue filed)

- **Panel diversity** — family-level model grouping (exact-model only today,
  so `gpt-4o` vs `gpt-4o-mini` read as different estimators) and a "diversify"
  auto-suggest helper.
- **ToT expansion refines in place** (stable labels keep re-scoring
  meaningful); add a child-generation variant if the beam starves.
- **Double Crux loop-backs re-enter `hunt_cruxes` only**, never `positions`,
  even when vague positions are why hunting fails.
- **MCDA free-text weights only parse `(weight: N)` / `[weight = N]`**;
  prose weights silently become `DEFAULT_WEIGHT` (humans only — the AI path is
  structured). A UI hint would close the gap.
- **#28 follow-ups** — per-claim citation mapping; opting in Adversarial Collab
  `gather_evidence` and ACH `present_evidence`; a richer source-picker UI; a
  live browser click-through of the Attach-evidence button (verified statically
  only — there is no JS test harness in this repo).
- **Packaging** — `make_icns.sh` needs Pillow on system python3; icon bubbles
  blur at 16–32 px.

### Open GitHub issues

#139 registration reveals that an email has an account; #128 unverified OAuth
sign-up can pre-hijack an address (both want an email-verification design);
#107 a rotation change spans several commits (crash mid-update desyncs members
and `method_state`); #102 follow-ups from the PR #100 review (Retry-After,
`doc_add` ingest budget, DNS-rebinding pin, `_init_image_tools` catch, test
file size); #97 Seatbelt profile does not confine writes; #96 no UI flag when
Python or image tools fail to load; #92 tool error results echo raw exception
text to the model and UI (the `/api/<method>` 500 body does too — noted on the
issue); #81 persist parse fidelity/notes; #80 pre-#78 `'(Empty PDF)'`
documents. Suggested next: #107 (no decision needed); #128/#139 need the repo
owner's call on email verification.

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
