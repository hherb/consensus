# Contracts to keep: authentication and web server

Moved verbatim from `HANDOVER.md` (2026-11-25) to keep the handover short.
Each rule was found by a characterization net during the #61 split or in
the follow-up issues named; keep them when touching `consensus/auth/` or
`consensus/server/`.

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
  repo owner's choice). It sets the cookie on every reply that has a
  session, errors included (the capacity 503 has none). Every other `/api/`
  route goes through `session_app()`, which uses `SessionManager.lookup()`
  (never creates) and answers 401 or 400 `NO_SESSION_ERROR`.
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
