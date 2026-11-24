# HANDOVER

_Last updated: 2026-11-25. `main` is at **v2.0.0** (released 2026-07-20)
plus everything merged since (the #61 split, auth hardening, the desktop
bridge fix #125, MCP argument validation #124, one-transaction rotation
#107, rotation rollback #147, `serialize_method_state` raising #150, failed
tool packages shown in the UI #96, `method_state` save consistency #152,
degraded-extraction warnings #81 — details in git history). This branch,
`fix/tool-error-text-92`, stops tool errors and the API's 500 echoing raw
exception text (#92). The suite is at **4563 passing**._

Briefs the next session on what is open and the conventions to keep;
per-PR detail lives in git history and `docs/superpowers/`.

## Where the project stands

| Area | State |
|------|-------|
| Discussion methods | 18 methods, all reviewed/repaired; composable `PhaseHandler` library (68 handlers) |
| Structured outputs | Forced tool calls for every structured phase; humans get a schema-driven form (#57) |
| Distribution | `consensus-app` on PyPI; notarized + stapled macOS DMG; v2.0.0 is the current stable |
| Website | `website/` — static site on Cloudflare Pages at https://consensus-ai.org/, incl. a discussion-methods catalogue (`methods.html`, PRs #82/#83; `_headers` cache-busts CSS) |
| Tests | 4563 passing (`uv run pytest`, ~60 s, offline; runs in `.venv` via the `dev` group) |
| Docs | README, QUICKSTART, user manual and `docs/devel/` aligned with the code (PR #62) |

### Merged campaigns (detail in git history)

Method review & repair (#12–#30, #42, #48, #56); NGT, MCDA, Double Crux, ToT
(#24–#27); evidence-tracked phases (#28, soft by design —
`memory/evidence-gating-philosophy.md`); structured human input (#57, #59,
#60); method-flow E2E tests (`tests/test_method_flow_e2e.py`); provider
resilience (PR #64, #65 — see ROADMAP); distribution
(`memory/alpha-distribution-release-process.md`); failure visibility
(#71–#74, #78); lint gate (#86) — contracts below.

## Flow error visibility (#71–#74, merged — keep these contracts)

- **Three kinds, one classifier.** `helpers.classify_flow_error` returns
  `ERROR_KIND_PROVIDER` / `_CONFIG` / `_INTERNAL` (`is_provider_error` is a
  predicate over it); only `internal`, the default, asks the user to file a
  bug. Catch `ConnectionError`, never its `OSError` base (a missing file is
  our fault), and **type the fault where it happens** — `AIClient` raises
  `AIResponseFormatError` at its parse sites (an HTTP 200 HTML body is
  otherwise a bare `KeyError`); `models.ConfigurationError` means "set up
  wrong". **Add new failure modes to the classifier, not to the wording**
  (`turns._SKIP_NOTICES` and `static/turn-notices.js` are keyed by kind).
- **`helpers.post_notice`** appends the in-memory `Message` first, guards
  `db.add_message`, returns `(message, persisted)` and appends
  `UNSAVED_NOTICE_SUFFIX` on a failed write; callers surface `notice_unsaved`.
- `conclude_discussion` returns `conclusion_error` and posts a notice (the
  discussion still concludes); `mediate` posts one too.
- **A failed Triage recommendation is never presented as one**:
  `MethodRecommender.recommend` raises `RecommenderError`; both failure paths
  in `run_triage_recommender` go through `_record_recommender_failure`.
- Non-UI consumers keep the error: `mcp_server.run_discussion` returns
  `conclusion_error` (matching the synthesis on `MessageRole.MODERATOR`);
  `evaluation/runner` sets `result.error`.
- **Every new result key has a production consumer** — a key only tests
  read is a contract nobody honours.
- Retries with backoff live in `ai_client._post_with_retry`; the flow layer
  sees only exhausted failures.

**Testing lesson:** mock the `AIClient`, never the method under test.
Tests: `tests/test_flow_error_*.py`.

## Document failure visibility (#78, PR #79 — keep these contracts)

Ten `tools_document/` defects shared one shape: an error became
plausible-looking content in a non-error `ToolResult`, read by the AI as
fact. Keep these before touching the package:

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
- **A degraded extraction is persisted and shown on every read** (#81):
  `documents.fidelity` (`models.ParseFidelity`: `full`/`degraded`) and
  `parse_notes` (JSON list), migration 017; `add_document` rejects an unknown
  value or `degraded` without a note. `handlers_rag._extraction_warning` is
  the one Python wording (`static/documents.js` `_docWarnings` copies it, so
  change both): `doc_list` prints it under the entry (all three modes — the
  search mode copies fields, so add new ones there), `doc_ask` and
  `doc_summary` return it as `extraction_warning`, and `_with_warning`
  prefixes it to the text `doc_get_text`, `doc_get_chapter`, the MCP
  `read_document` and the nothing-found replies return; MCP
  `search_documents` tags each degraded hit and `list_documents` returns
  both fields. `_docWarnings` shows it and a failed summary in the Documents
  panel. Migration 018 flags the pre-#78 `'(Empty PDF)'` rows degraded,
  leaving their text and chunks alone (#80). Other rows older than 017 read
  as `full`: their parse was never recorded.
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
below threshold" in `doc_ask`; no summary regeneration, OCR or charset
detection; `INDEXING_RETRY_INTERVAL` hardcoded at 60 s; pre-#78
`'(Empty PDF)'` documents are flagged but still answerable (#80).
Tests: `tests/test_tools_document_failures.py`, `tests/test_document_fidelity.py`
(each wiring mutation-checked), `tests/test_background.py`,
`tests/test_tools_document*.py`.

## Unexpected errors name only their class (#92 — keep this contract)

An exception's message can carry an endpoint URL with a credential in its
query string (httpx) or an internal file path (SQLite; matters in
`--multi-user`). So the generic catch-alls show `error_text.public_error`
(`"<summary> (<ExceptionClass>)"`) and log the full exception: the two
`Tool <name> failed` fallbacks in `tools.py`, `tools_memory` `_tool_failure`,
and `handle_api`'s 500 (`<method> failed (…)`). Repo owner's call
(2026-11-25): the same in every mode, local ones included. Errors we word
ourselves (validation 400s, auth replies, typed `DocumentError`s with their
hint) keep their text. The remaining catch-alls (memory config and
connection test, `recommend_method`, the MCP server's generic `Error: …`)
are **#157**. Tests: `tests/test_tools.py`
(`SECRET`), `tests/test_server_api.py`, `tests/test_tools_memory_*.py`.

## Speaking rotation after start (#104 — keep this contract)

`app_discussion_rotation.update_rotation(discussion, db, restore, add=, remove=)`
is the only code that changes the rotation of a started discussion;
`add_to_discussion`, `remove_from_discussion` and `set_participant_role` all
route through it (`reorder_da_in_turn_order` is gone). It keeps four copies in
step — `turn_order`, `base_turn_order`, `method_state` `_turn_order` /
`_turn_index`, and `discussion_members.turn_position`. **All the DB writes of
one change are one transaction** (#107): `update_rotation` hands
`db.save_rotation` the joiner's insert or the leaver's delete, every role (so
a devil's-advocate swap commits both, and `revert_other_devils_advocates`
changes memory only), the positions and `method_state`, and it rolls back on
any failure; callers must not write the member row or role themselves. **A
failed save leaves memory as it was** (#147): callers take
`restore = snapshot_rotation(discussion)` *before* their first edit and pass
it in; on failure `update_rotation` calls it, logs, and returns an error
string the caller returns as `{"error": ...}` (the setup UI toasts it). Side
effects outside the rotation (devil's-advocate tools via `assign_da_tools`,
the join/leave notice via `post_notice`) run only after a successful save, and
a failure there is a transcript notice, never an error for the saved change. A
position update that matches fewer rows than the order names is logged as
drift. Rules: the devil's advocate is last in both orders; a reorder never
changes whose turn it is (`carry_turn_index`; a departed holder passes the
turn to the next remaining member); the `_turn_index` stamp is re-pointed at
the carried live index, not dropped, so a reload still restores a #19
mid-round reset; a joiner enters the current phase only when that phase runs
the whole roster — a narrowed phase (moderator-only, red team) is left alone
until the method next derives the order (normally the next transition); a
removal that would empty the current phase falls back to the roster, as
`apply_method_turn_order` does. Only a **live** (active/paused) discussion
persists any of this (`LIVE_STATUSES`): edits to a concluded discussion still
in memory come from the setup screen after a refresh and are input for the
next `start_discussion`, so they stay in memory and never rewrite the
concluded record. Removing a non-member is an error. Tests:
`tests/test_app_discussion_rotation.py` (each rule mutation-checked).

**`serialize_method_state` raises `ValueError`** on a value JSON cannot
encode (#150); it used to return `"{}"`, which its callers committed over
the stored state. Never add a fallback there: the rotation rollback,
`generate_ai_turn`'s internal-error notice and the API's 500 → `{error}`
(desktop: a rejected promise, which `completeTurnFlow` must treat as a
stop, or the auto-run loop repeats paid turns) are what report it. Store
only JSON types in `method_state` (no sets).

**Every live-discussion save goes through `helpers.save_method_state`**
(#152), incl. `_increase_budgets`; rotation keeps its own rollback, and a
`start_discussion` whose method init or save raises anything soft-deletes
its row, clears `method_state` and keeps setup images pending. On a
`ValueError` the helper writes nothing, resets `_DB_BACKED_FIELDS` (state,
method, orders, turn position, status, budgets) to what
`app_discussion_state.discussion_from_db` (a reload) builds, and re-raises
(a failed reload is logged, never raised over it); messages stay, so an
unsaved notice stays visible. So **post your own notice only after the
save**: `complete_turn` and the blocked Triage handoff do;
`generate_ai_turn` saves once, after the recommender. (The successful
handoff and retry save after `switch_discussion_method` has already
announced and saved the switch.) Tests:
`tests/test_method_state_save_failure.py` (mutation-checked for every site
but the retry's blocked re-pause, whose record is JSON-safe).

## Tool packages that fail to load (#96 — keep this contract)

`ConsensusApp` sets `memory_available`, `documents_available`,
`images_available` and `python_tools_available` in its `_init_*_tools`;
`get_state()["tool_load_failures"]` lists the labels of those that are
`False`, and `static/profiles.js` `_toolLoadNotice()` renders it above the
profile tool list (their tools are otherwise just absent). A new tool
package adds its flag to that tuple. All four `_init_*_tools` catch
`Exception`, so a faulty provider is listed rather than crashing startup.
Document tools import `tools_memory`, so a broken memory package lists both.
Tests: `tests/test_app_characterization.py` (`tool_package` / `tool_load` /
`tool_provider`).

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

- **Email is never user-editable** (`update_user`: `display_name` /
  `avatar_url` only). Nothing verifies a new address and OAuth auto-links a
  verified email, so an edit is an account-takeover path.
- **A verified OAuth link to a password account clears the password and
  revokes every session** in the link's transaction
  (`link_oauth(..., lock_out_password=True)`; repo owner, 2026-11-15). An
  unverified provider email never links; an unverified OAuth *sign-up* can
  still squat an address (**#128**).
- An OAuth reply without an email *or* provider user id is refused; ids go
  through `oauth._provider_id` (a JSON `null` stays empty, not `"None"`).
  `link_oauth` keeps the legacy-column identity in `user_oauth_identities`.
- Provider requests use `oauth._send_with_retry` (shared `http_fetch`
  policy). A failed GitHub email lookup sets `email_lookup_failed` (user is
  told to retry). Providers are offered only with client ID *and* secret.
- **No `oauth` in names that reach a log call** (`SIGN_IN_PROVIDERS`, …):
  CodeQL's password heuristic matches it and blocks merging (PR #129); check
  with the `Security/CWE-312/CleartextLogging.ql` query.
- `create_token` / `store_oauth_state` prune expired rows; `login` checks
  the password before saying "disabled".
- **Hashes record their cost** (#127): `pbkdf2_sha256$<iter>$<salt>$<hash>`;
  legacy `salt$hash` means `LEGACY_PASSWORD_HASH_ITERATIONS` (600k, never
  change). `login` re-hashes on `needs_rehash` via compare-and-swap
  (`change_password(..., expected=)`); a failed write is logged, never a
  failed login. Downgrading locks password users out.
- **Unknown emails cost a wrong password** (#131): `burn_password_check`.
  Registration still reveals a taken email (**#139**).
- **Hashing runs off the event loop** (#141): `routes_auth._hash_off_loop`,
  at most `PASSWORD_HASH_CONCURRENCY` (`ServerContext.password_slots`). The
  handler **counts an attempt before hashing** and removes it on success.
  `AuthDatabase` reads use `_query_one` under the write lock; login's
  `create_token(password_hash=...)` requires the verified hash and
  `is_active` in one statement.
- **An OAuth exchange has a deadline** (#130): `SIGN_IN_DEADLINE_SECONDS`
  (45 s, under nginx's 60 s), raised before any user or token is written.

Tests: `tests/test_auth_*.py` (100% coverage; providers faked at the httpx
transport via `tests/auth_helpers.install_routes`; patch through
`auth_helpers.module_of`; `fast_hashing` lowers PBKDF2 rounds).

## Web server (`server/`, #61 slice 16 — keep these contracts)

`create_web_app(host, port, multi_user)` builds the app, `launch_web` serves
it; all per-server state is one `ServerContext` under `CTX_KEY`, read by
module-level handlers. Tests drive the real middleware through aiohttp's
`TestClient` (`tests/test_server_*.py`, fixtures in `tests/server_helpers.py`).

- **CSRF:** a POST to `/api/` or `/auth/` must be JSON or carry
  `X-Consensus-Request` (`UPLOAD_HEADERS` in `static/api.js`);
  `test_frontend_posts_pass_the_csrf_guard` fails on a new bare `fetch` POST.
  PUT/DELETE carry no check by design (no preflight is ever granted).
- **Uploads read through `request_io.read_upload`**, lifting the 1 MiB body
  limit for that read only; its size constants are imported inside the
  handlers so a broken optional backend cannot stop startup.
- `auth_middleware` resolves the user for `/auth/me` too (enforces only on
  `/api/*`).
- Bodies go through `read_json_object` (non-object → 400); malformed ids,
  non-string credentials/profile fields/titles are 400s
  (`request_io.non_string_field`); `CAPACITY_ERROR` is the one capacity text.
- **`/api/<method>` entries build a `functools.partial`, never call** (#134):
  `handle_api` binds it against the signature first, so bad arguments are a
  400 `Invalid arguments for <method>: …` and only an exception *inside* the
  method is a logged 500 (wording: #92 above). Numbers go through
  `request_io.int_field` / `float_field`.
- **The desktop bridge is never pywebview's `js_api`** (#125): its dotted-name
  `getattr` walk reached `_app.db`. `launch_desktop` registers
  `exposed_functions(bridge)` with `window.expose`; `TestDispatch` in
  `tests/test_desktop_bridge_api.py` proves private paths do not resolve.
- **Only `POST /api/<method>` creates a `--multi-user` session** (#132) and
  sets the cookie on every reply that has one; other `/api/` routes use
  `session_app()` → `SessionManager.lookup()` (401 / `NO_SESSION_ERROR`).
- **SSE ends once its session is evicted** (#135): each keepalive checks
  `lookup(sid, touch=False) is app_instance` (an open tab must not keep a
  session alive), sends `session_expired`, and `static/app.js` re-creates the
  session via `get_state` with backoff from `SSE_REOPEN_DELAY_MS` up to
  `SSE_REOPEN_MAX_ATTEMPTS` (verified by reading only).
- `run_migrations` re-checks the file (an evicted session's DB re-created at
  the same path used to come back schema-less).
- `SessionManager` expiry and `stop()` share `_close_app()`; apps close after
  the lock is released; session ids in logs are truncated; `prune_stale()`
  prunes `login_attempts` / `rate_limits`.
- Tests patch a setting where it is read (`middleware.RATE_LIMIT_MAX`, …).
  **A new route goes in `app_factory.py` and `tests/test_server_facade.py`'s
  table; a new dispatch entry in `tests/test_server_api.py` `DISPATCH`**
  (read by AST, must name exactly the table's keys).

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

**Watch:** `pricing.py` (498), `app_discussion_state.py` (488) and
`tools_document/handlers_rag.py` (481): split
before the next addition. `static/discussion-actions.js` (506) and
`evaluation/static/eval.js` (645) are over the cap; split them when next
touched.

**Facade guards** (`tests/test_<name>_facade.py`) pin each package's `__all__`
by identity and AST-scan call sites (a dropped lazy re-export fails only in
production); keep them in step.

**Recipe for a split** (worked for all sixteen): (0) check coverage
(`uv run --with pytest-cov pytest -q --cov=consensus.<module> --cov-report=term-missing`;
a green suite over an untested module proves nothing) and commit a
characterization net — real `Database` (`tmp_db`), fakes only at network/GUI
edges, driving the entry point production uses — *before* a line moves, fixing
each defect it finds in its own commit (drifted duplicate logic was the usual
bug); (1) move by line range with a script that checks every moved line
reappears once; (2) fix relative-import depth, **including imports inside
function bodies**, and `__file__` paths; (3) re-export from `__init__.py` and
point test patches at the submodule that reads the name; (4)
`uvx ruff check --select F <pkg>`, full suite; (5) add the facade guard and
watch a dropped re-export fail; (6) **mutate the new wiring and watch it
fail** (coverage measures lines executed, not behaviour constrained); (7)
update every doc naming the old file *and* the content beside the name.

### Deferred follow-ups (no issue filed)

- Panel diversity: family-level model grouping (`gpt-4o` vs `gpt-4o-mini`
  count as different estimators today) and a "diversify" auto-suggest.
- ToT expansion refines in place (stable labels keep re-scoring meaningful);
  add child generation if the beam starves.
- Double Crux loop-backs re-enter `hunt_cruxes` only, never `positions`, even
  when vague positions are why hunting fails.
- MCDA free-text weights parse only `(weight: N)` / `[weight = N]`; prose
  weights silently become `DEFAULT_WEIGHT` (humans only — the AI path is
  structured). A UI hint would close it.
- #28: per-claim citation mapping; opting Adversarial Collab
  `gather_evidence` and ACH `present_evidence` into evidence tracking; richer
  source picker.
- Packaging: `make_icns.sh` needs Pillow on system python3; icons blur at
  16–32 px.
- No JS test harness: UI-only behaviour (Attach-evidence button, SSE reopen,
  the #96 tool-load notice, the #81 Documents-panel warnings) is verified by
  reading only.

### Open GitHub issues

- Need the repo owner's call: #156 whether to flag pre-017 rows holding
  U+FFFD as degraded, and whether `add_document`'s `fidelity` becomes
  required; #80 whether to drop the chunks of the flagged
  pre-#78 `'(Empty PDF)'` documents so `doc_ask` stops answering from them
  (the rows now warn; deleting stored content was left to the repo owner);
  #146 stricter `consensus-mcp` argument validation (unknown args, `null`
  identity args, numeric bounds); #139 registration reveals a taken email
  and #128 an unverified OAuth sign-up can pre-hijack an address (both want
  an email-verification design).
- No decision needed: #102 PR #100 review follow-ups; #97 Seatbelt does
  not confine writes; #157 the catch-alls #92 left. Suggested next: #157.

### Roadmap

Nearest ⬜ items in `ROADMAP.md` are Argument Mapping, Tournament /
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
  the same commit). The composer stays open while paused and posts to
  `submit_human_message`, which records the message but deliberately skips
  `get_active_method` / `process_response` while paused: the turn does not
  advance, so the composer accepts repeated sends, and a re-run could
  double-record a vote. The contribution is processed after Resume.
  `submit_human_structured_message` keeps a full `is_active` check: a
  structured payload writes `method_state`, and its form is never mounted
  while paused.
- **Lint gate (#86):** `tests/test_lint.py` fails on any `ruff --select F`
  finding in `consensus/` (not `tests/`). `pytest-asyncio` 1.x runs in
  `asyncio_mode = auto`: write async tests as `async def`.
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
