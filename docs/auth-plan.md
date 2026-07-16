# Chronos — Role-Based Auth Enforcement Plan

**Status:** DRAFT — pending review
**Author:** Daniele Angeli (planning session with OpenClaw, 2026-07-14)
**Scope:** Add end-user identity + role enforcement to `gluesync-scheduler-module`
(chronos) REST endpoints. Complements the UI blockage shipped in
`gluesync-nodejs-monorepo` MR !407 and the Kotlin API hardening in
`gluesync-kotlin` MR !2090.

---

## 1. Current state (facts)

- **Chronos is a Python FastAPI service** running behind Traefik at
  `chronos/api/*` on the same origin as CoreHub. It is **not** proxied
  through CoreHub itself.
- Chronos already authenticates *itself* to CoreHub as an **external
  module** via `/ext-module` WebSocket using
  `Module-License` + `Module-Tag` headers. It receives a **module JWT**
  used only for outbound calls to CoreHub. `GluesyncSDKClient.token`
  holds it.
- **Chronos has zero end-user auth today**:
  - `gluesync_scheduler/core/app.py` mounts CORS `allow_origins=["*"]`,
    no auth middleware, no `Depends(...)` guard on any route.
  - Routers (`router.py`, `pipeline_router.py`, `settings_router.py`,
    `webhook_router.py`) accept every request unauthenticated.
- **The browser already sends the CoreHub session cookie to chronos**
  because they share the same origin (Traefik):
  - Cookie name: `gs-auth`
  - Set by CoreHub as `HttpOnly` (`corehub/security/apitoken/JwtCookie.kt`)
  - Contains the CoreHub-issued user JWT (HMAC256, `iss=MOLO17`,
    claims: `sub` = username, `role`, `jti` = session id, `plan`,
    `timestamp`, `change_required`).
- **Server-side session validation is stateful in CoreHub**
  (`ApiTokenManager.onValidationRequired` calls
  `userSessionManager.validateSession(username, session=jti)`).
  A JWT alone cannot be trusted — sessions can be revoked, and the JWT
  outlives them until timestamp expiry. Local verification with the
  shared secret would allow revoked-session bypass.
- **CoreHub exposes `GET /auth/me`** (`AuthenticationRoutes.kt:236`)
  which:
  - Requires the `gs-auth` cookie (or `Authorization: Bearer`)
  - Runs the full stateful validation (JWT signature + issuer + session
    liveness + extend-on-hit)
  - Returns `UserResponseDto { username, role, ... }`.
- Available Python deps: **PyJWT, httpx, cryptography** already in
  `requirements.txt`. No new install needed.
- Kotlin `Authorization.requirePermission { ... }` model in
  `corehub/security/Authorization.kt` uses lambdas over
  `UserRole` receiver. That's the pattern to mirror.

---

## 2. Design choice — how chronos learns the user

Two viable strategies. **Recommendation: Strategy A (CoreHub introspection).**

### Strategy A — Delegate to CoreHub `/auth/me` (RECOMMENDED)

Every incoming request that hits a protected route triggers a call to
CoreHub `GET /auth/me`, forwarding the incoming `gs-auth` cookie (or
`Authorization: Bearer` header). Response → user + role. Cached
short-term to keep latency sane.

**Pros**
- **Correct.** Honours session revocation, password changes, role
  changes, OIDC state — CoreHub is the single source of truth.
- **Zero new secret to distribute.** No need to leak `apiTokenSecret`
  to chronos.
- Works transparently with OIDC + cookie sessions.
- Small code footprint: one HTTP call + TTL cache.

**Cons**
- Adds ~1–5 ms per request to CoreHub (dominant when local; ~5–20 ms if
  chronos is on a different host). Mitigated by cache.
- Requires CoreHub to be reachable from chronos on the CoreHub-URL
  chronos already discovers (`gluesync_sdk_client.corehub_url`).
- Cache TTL becomes a security knob (revoke-latency ≤ TTL).

### Strategy B — Locally verify JWT with shared HMAC secret

Provision `apiTokenSecret` (or a dedicated secret) into chronos, verify
the JWT locally with PyJWT.

**Pros**
- Zero-latency verification.
- No CoreHub round-trip.

**Cons**
- **Cannot detect revoked sessions** without also implementing
  `userSessionManager.validateSession` semantics — chronos would either
  duplicate that logic against a shared session store or ignore it and
  accept revoked JWTs until expiry. Neither is acceptable for a security
  fix.
- New secret distribution burden (env var / license file / shared
  volume).
- Signature-algorithm coupling between chronos and CoreHub. Any future
  rotation to RSA/asymmetric or JWK is a coordinated change.

### Strategy C — Hybrid (local verify + periodic freshness call)

Verify locally, refresh session-liveness via CoreHub every N seconds
per `(user, jti)`. More moving parts, not warranted for this scope.

**Decision:** Go with **A**. If perf becomes a problem later, switch to
**C** as an optimisation — API surface stays identical.

---

## 2.5 How does authorization actually happen? (step-by-step)

Before the design details, the concrete request/response flow for a
single user action. This is what will happen every time the browser
calls `chronos/api/*` after this feature ships.

### Step 1 — Browser has the current user token

When the user logs in through the CoreHub SPA, CoreHub sets an
`HttpOnly` cookie named **`gs-auth`** containing the user JWT
(`AuthenticationRoutes.kt:96` → `call.setAuthCookie(token)`). The
browser cannot read it (HttpOnly), but sends it with every request to
the same origin.

### Step 2 — User accesses chronos → cookie travels automatically

The SPA calls `chronos/api/jobs`. Because chronos is served on the
**same origin** as CoreHub (via Traefik), the browser **automatically
attaches** the `gs-auth` cookie to the request. The React app does
nothing special — the shared axios instance already runs with
`withCredentials: true` (see `packages/apps/gluesync-ui/src/shared/helpers/request/request.ts:47`).

Chronos receives, for example:

```http
GET /api/jobs HTTP/1.1
Host: hq.molo17.com
Cookie: gs-auth=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkYW5pZWxlIiwicm9sZSI6Ik1BTkFHRVIiLCJqdGkiOiIuLi4iLCJpc3MiOiJNT0xPMTcifQ.SIGNATURE
```

Equivalently: `Authorization: Bearer <token>` for non-browser clients
(CLI, scripts) — CoreHub itself accepts both, and so will chronos.

### Step 3 — Chronos verifies the token against CoreHub

**Not** via the Python SDK. The `gluesync-sdk` is for
module-authenticating-as-a-module (`/ext-module` handshake with
licence + tag). It has no "validate an end-user token" primitive.

Instead, chronos makes a plain HTTP call to CoreHub's existing
introspection endpoint — **`GET /auth/me`** — forwarding the incoming
caller's cookie (or `Authorization` header) verbatim:

```http
GET /auth/me HTTP/1.1
Host: <corehub-host>
Cookie: gs-auth=<forwarded from the incoming chronos request>
```

CoreHub's `/auth/me` does the full stateful validation:

1. HMAC256 JWT signature check with `apiTokenSecret`.
2. Server-side session liveness via `userSessionManager.validateSession(username, jti)`.
3. Returns `200 { username, role, id, ... }` on success, `401` if the
   token is expired, revoked, or invalid.

This matters: **CoreHub is the source of truth**. Local JWT decoding
by chronos would happily accept a JWT whose backing session was
revoked; only the round-trip catches that.

Base URL for the call is `gluesync_sdk_client.corehub_url`, discovered
during the SDK handshake at chronos startup (`app.py:376`). No new
config needed for URL discovery.

### Step 3.5 — Session cache (between chronos and CoreHub)

Hitting `/auth/me` on every single request would be wasteful when the
user is clicking around the scheduler UI. So chronos caches the
introspection result:

- **Key:** the token itself (cookie value or bearer). Different tokens
  for the same user cache independently — one may have been revoked
  without the other.
- **Value:** `CurrentUser { username, role }`.
- **TTL:** 30 seconds by default. Tunable via `CHRONOS_AUTH_CACHE_TTL`.
- **What gets cached:** only successful `200` responses.
- **What does NOT get cached:** `401` / `403` / network errors. Every
  failure re-checks — no negative caching.

Revoke-latency ≤ TTL: a user who logs out (or has their session
invalidated in CoreHub) keeps chronos access for at most 30 s before
the next request re-checks and gets 401. Acceptable for the threat
model; the knob is there if we ever need it tighter (0 = never cache,
for debugging).

### Step 4 — Authorized (or not)

On a cache hit or a fresh 200 from CoreHub, chronos has
`CurrentUser { username, role }`. FastAPI's `Depends(require_manage /
require_control / require_config)` then checks the role against the
endpoint's required permission:

- **Pass** → route handler runs. `user.username` is available for audit
  logging.
- **Fail** → `HTTPException(status_code=403, detail="Role X cannot ...")`.

On a `None` return from the introspector (missing cookie, expired
token, CoreHub unreachable):

- **`HTTPException(status_code=401, detail="Not authenticated")`.**

**Chronos never fails open.** A misconfigured or unreachable CoreHub
results in 401s, not blanket access. The only exception is the
explicitly-labelled **debug-only** `CHRONOS_AUTH_FAIL_OPEN=true` escape
hatch (§8) — never to be set in production.

### One-liner mental model

> Chronos doesn't check the token; **it asks CoreHub who the token
> belongs to**, and CoreHub is the sole authority. Chronos then only
> decides *what that role is allowed to do* on the scheduler.

---

## 3. Target architecture (Strategy A)

```
┌────────────┐   gs-auth cookie   ┌──────────┐   GET /auth/me     ┌──────────┐
│  Browser   ├───────────────────►│ Chronos  ├───────────────────►│ CoreHub  │
│  (SPA)     │                    │ FastAPI  │  (fwd cookie/hdr)  │  /me     │
└────────────┘                    └──────────┘◄───────────────────┴──────────┘
                                       │      { username, role, ... }
                                       ▼
                                   Depends(require_role(...))
                                   ├─ MANAGE  (create/edit/delete)
                                   ├─ CONTROL (run/pause/enable)
                                   └─ CONFIG  (settings)
```

## 4. Module design

### 4.1 New module: `gluesync_scheduler/security/`

```
gluesync_scheduler/security/
  __init__.py
  user_role.py          # UserRole enum + permission helpers
  auth.py               # FastAPI dependency: current_user, require_*
  corehub_introspect.py # httpx client with TTL cache
  exceptions.py         # PermissionDeniedError, UnauthenticatedError
```

### 4.2 `user_role.py`

Mirror the Kotlin `UserRole` enum:

```python
from enum import Enum

class UserRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    MANAGER     = "MANAGER"
    MONITOR     = "MONITOR"
    VIEWER      = "VIEWER"
    EXTERNAL_MODULE = "EXTERNAL_MODULE"

def can_manage_schedules(role: UserRole) -> bool:
    """Create / edit / delete schedules."""
    return role in (UserRole.SUPER_ADMIN, UserRole.MANAGER)

def can_control_schedules(role: UserRole) -> bool:
    """Run-now, enable/disable existing schedule."""
    return role in (UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.MONITOR)

def can_modify_configuration(role: UserRole) -> bool:
    """Timezone, chained events, other global settings."""
    return role == UserRole.SUPER_ADMIN  # matches CoreHub Authorization.kt
```

> Note: `canModifyConfiguration` is **SUPER_ADMIN only** in CoreHub. The
> UI MR !407 grants it to both SUPER_ADMIN and MANAGER per the existing
> `UserRolePermissions.canModifyConfiguration=true` for MANAGER. That's
> a **UI vs backend mismatch that already exists** for other config
> endpoints — flagged separately, not solved here. Chronos will match
> the UI's expectation (MANAGER can save timezone) OR match CoreHub
> (SUPER_ADMIN only). **Decision needed** — see §9.

### 4.3 `corehub_introspect.py`

```python
import httpx, time
from dataclasses import dataclass
from typing import Optional
from asyncio import Lock
from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: UserRole

_CACHE: dict[str, tuple[CurrentUser, float]] = {}
_CACHE_LOCK = Lock()
_TTL_SECONDS = 30  # tunable via env CHRONOS_AUTH_CACHE_TTL

class CoreHubIntrospector:
    async def introspect(self, cookie_header: str | None, auth_header: str | None) -> Optional[CurrentUser]:
        cache_key = auth_header or cookie_header
        if not cache_key:
            return None
        # 1) cache hit
        cached = _CACHE.get(cache_key)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]
        # 2) call CoreHub /auth/me
        base = gluesync_sdk_client.corehub_url  # already discovered
        if not base:
            return None
        headers = {}
        if auth_header:  headers["Authorization"] = auth_header
        if cookie_header: headers["Cookie"] = cookie_header
        async with httpx.AsyncClient(timeout=5.0, verify=_ssl_verify()) as c:
            r = await c.get(f"{base}/auth/me", headers=headers)
        if r.status_code == 401 or r.status_code == 403:
            return None
        r.raise_for_status()
        body = r.json()
        user = CurrentUser(username=body["username"], role=UserRole(body["role"]))
        async with _CACHE_LOCK:
            _CACHE[cache_key] = (user, now + _TTL_SECONDS)
        return user
```

- Uses `gluesync_sdk_client.corehub_url` (already available at startup).
- TTL cache: **default 30 s**. Revoke-latency ≤ 30 s is acceptable for
  the threat model (a revoked VIEWER cannot exceed 30 s of scheduler
  write access after logout). Configurable via `CHRONOS_AUTH_CACHE_TTL`.
- Cache keyed by token; simple dict — swap to `cachetools.TTLCache` if
  we ever need eviction under memory pressure.
- SSL verify: reuse `chain_execution_service`'s helper
  (`_ssl_verify()`), which already respects `SKIP_TLS_VERIFICATION`.
- Failure modes:
  - `401/403` → return `None` → 401 back to client.
  - `5xx` / connection error → **fail closed** (401), log warning.
    Never fail open on introspection error.

### 4.4 `auth.py`

FastAPI dependencies:

```python
from fastapi import Depends, HTTPException, Request

async def current_user(request: Request) -> CurrentUser:
    cookie = request.headers.get("cookie")
    authz  = request.headers.get("authorization")
    user = await _introspector.introspect(cookie, authz)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def require_manage(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not can_manage_schedules(user.role):
        raise HTTPException(status_code=403, detail=f"Role {user.role.value} cannot manage schedules")
    return user

def require_control(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not can_control_schedules(user.role):
        raise HTTPException(status_code=403, detail=f"Role {user.role.value} cannot control schedules")
    return user

def require_config(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not can_modify_configuration(user.role):
        raise HTTPException(status_code=403, detail=f"Role {user.role.value} cannot modify configuration")
    return user
```

Read endpoints: `Depends(current_user)` (any authenticated user with an
allowed role — includes VIEWER + MONITOR).

Any request without a valid cookie/bearer is rejected with **401**,
regardless of role.

---

## 5. Endpoint-by-endpoint matrix

| Method | Path (under `/api`) | Guard | Notes |
|---|---|---|---|
| `GET`    | `/jobs`                    | `current_user`  | any authenticated |
| `POST`   | `/jobs`                    | `require_manage`| create |
| `GET`    | `/jobs/{id}`               | `current_user`  | |
| `PUT`    | `/jobs/{id}`               | `require_manage`| edit |
| `DELETE` | `/jobs/{id}`               | `require_manage`| delete |
| `POST`   | `/jobs/{id}/run`           | `require_control`| run-now |
| `PATCH`  | `/jobs/{id}/status`        | `require_control`| enable/disable |
| `GET`    | `/settings/timezone`       | `current_user`  | read |
| `PUT`    | `/settings/timezone`       | `require_config`| |
| `GET`    | `/settings/*` (other)      | `current_user`  | read |
| `PUT/POST/DELETE` `/settings/*` | `require_config` | writes |
| `*`      | `/pipelines/*`             | **`verify_localhost` only** (chronos-internal, cron-triggered; see below) |
| `*`      | `/webhooks/*`              | Not present on `main`; ships with `feature/chained-events`. Auth model deferred to that MR. |
| `POST`   | `/jobs/chained/*`          | Deferred with `feature/chained-events`. |

Exact per-route wiring implemented in iteration 2 (this MR).

**Note on `pipeline_router.py`.** The pipeline actions
(`/pipelines/{id}/play|pause|redo|one-time-snapshot|enter-maintenance|exit-maintenance`)
are marked **INTERNAL USE ONLY** in their docstrings and already have a
`verify_localhost` router-level dependency. They are invoked by
chronos's own cron jobs, which cannot present an end-user JWT, so the
user-role guard model does not apply here. They remain protected by
the existing localhost check. This is the same architectural pattern
as the deferred webhook receiver: the CoreHub-internal caller
(chronos cron in this case) authenticates itself by network origin
rather than by user identity.

---

## 6. Module-to-module traffic (internal callers)

Chronos endpoints are **also** called internally by other Gluesync
components in some flows. Two known cases to preserve:

1. **CoreHub → chronos webhooks** (chained events). Currently
   unauthenticated. Options:
   - Whitelist a bearer token stored in chronos config + sent by
     CoreHub in `Authorization: Bearer <internal-token>`. Skip role
     check when this token matches.
   - Or: mark the webhook receiver route(s) with a distinct dependency
     that only checks the internal-token header, not the user JWT.
   - **Preferred:** add an env `CHRONOS_INTERNAL_TOKEN`, dependency
     `internal_or_manage(...)` on the specific webhook endpoints.
2. **CLI / scripts.** If any script hits `/api/jobs*` today with a
   bearer JWT from a login call, they'll continue to work — same
   `Authorization: Bearer` path that the browser uses.

Audit `gluesync-kotlin` for any code that hits chronos and enumerate
callers before rollout (see §7.3).

---

## 7. Iterations

### Iteration 1 — Security scaffolding (no route changes)

- Create `gluesync_scheduler/security/` module (§4).
- Add unit tests for `UserRole` mapping + permission helpers.
- Add `httpx`-based integration test with a mocked `/auth/me`.
- No FastAPI route touched. No behaviour change.
- **Deliverable:** merged to `main`. Safe.

### Iteration 2 — Wire guards onto job + settings routes

- Add `Depends(...)` to every route in `router.py`, `settings_router.py`
  per §5.
- Preserve `pipeline_router.py` and `webhook_router.py` behaviour until
  §7.2/§7.3 audit is done.
- **Migration risk:** any client currently calling chronos without a
  cookie/bearer will get 401. This is the intended behaviour of the
  security fix, but must be announced in release notes.
- Add integration tests hitting each endpoint with:
  - No auth → 401
  - VIEWER token → 403 for writes, 200 for reads
  - MONITOR → 200 for control, 403 for manage
  - MANAGER → 200 for manage + control, 403 for config (or 200 if
    §9 decision goes MANAGER-can-config way)
  - SUPER_ADMIN → 200 everywhere
- **Deliverable:** MR to `main`, marked as **security-fix**, coordinated
  release with UI MR !407.

### Iteration 3 — Pipeline + webhook routers

- Audit each pipeline/webhook endpoint.
- Split "browser-facing" endpoints (need user role) from
  "module-to-module" (need internal token).
- Introduce `CHRONOS_INTERNAL_TOKEN` env + `internal_only(...)`
  dependency for CoreHub-originated webhook callbacks.
- Update `gluesync-kotlin` webhook caller to send the token.
- Deploy in lockstep (feature-flag via env: skip check if token
  unset — for backward compat during rollout, remove flag in
  Iteration 4).

### Iteration 4 — Hardening

- Remove backward-compat feature flag.
- Add rate-limiting on 401/403 responses (cheap DoS mitigation).
- Move JWT verification behind a middleware so 401s never touch route
  handlers.
- Add metrics: `chronos_auth_introspect_total{result}`,
  `chronos_auth_cache_hits_total`, `chronos_auth_denied_total{role,endpoint}`.
- Consider adding local JWT signature pre-check (fail-fast on tampered
  tokens) before the introspection call.

---

## 8. Configuration surface

New env vars (all optional with sane defaults):

| Env | Default | Purpose |
|---|---|---|
| `CHRONOS_AUTH_CACHE_TTL`      | `30`  | Seconds. TTL for the introspection cache. |
| `CHRONOS_AUTH_TIMEOUT_MS`     | `5000`| httpx timeout when calling `/auth/me`. |
| `CHRONOS_AUTH_FAIL_OPEN`      | `false` | **NEVER set to true in prod.** Debug only. Bypasses auth if `/auth/me` unreachable. |
| `CHRONOS_INTERNAL_TOKEN`      | *(unset)* | Shared secret for CoreHub → chronos webhook callbacks. Feature-flagged. |
| `CHRONOS_COREHUB_URL_OVERRIDE`| *(unset)* | Force `/auth/me` base URL instead of using SDK-discovered value. |

---

## 9. Open decisions (need answer before implementation)

1. **`canModifyConfiguration` mismatch.** UI grants it to MANAGER;
   CoreHub `Authorization.kt` grants it to SUPER_ADMIN only. Chronos
   must pick one:
   - **(a)** Match UI → MANAGER can save timezone. Consistent UX,
     but chronos becomes more permissive than CoreHub is for its own
     equivalent config endpoints.
   - **(b)** Match CoreHub → SUPER_ADMIN only. Then UI needs another
     patch to hide the settings gear for MANAGER too.
   - **Recommendation:** **(a)** — match the UI. The UI-vs-CoreHub
     drift on `canModifyConfiguration` is pre-existing and out of scope.
2. **Chained events / webhook receiver auth model** (§6, §7.3). Bearer
   token vs mTLS vs "shared cookie forwarded by CoreHub"? Simple bearer
   is proposed; confirm.
3. **Chronos → CoreHub URL discovery.** `gluesync_sdk_client.corehub_url`
   is set after SDK init. If chronos gets a request before SDK is
   ready, `/auth/me` cannot be called. Options:
   - Return **503** during warmup (fail closed).
   - Block route registration until SDK ready (delays startup).
   - **Recommendation:** 503 during warmup + healthcheck endpoint
     unguarded.
4. **Read-tier gating.** Should VIEWER see the schedule list at all? UI
   currently shows the tab to VIEWER (read-only). Backend should match:
   `GET /jobs` allowed for any authenticated role. **Confirmed by UI
   MR !407 behaviour** — proceed.
5. **Rollback plan.** If Iteration 2 breaks a customer deployment,
   `CHRONOS_AUTH_FAIL_OPEN=true` restores unauthenticated access.
   Document loudly.

---

## 10. Test plan

- **Unit tests** (`tests/security/`):
  - `UserRole` parsing (case, unknown values → error).
  - Every permission helper × every role.
  - Introspector cache: hit, miss, TTL expiry, negative cache absent
    (deliberately not cached; a 401 must not stick).
- **Integration tests** (existing `tests/` structure):
  - Mock CoreHub `/auth/me` with `respx`.
  - Every guarded endpoint × every role × auth-missing.
- **End-to-end** (manual, staging):
  - Login as VIEWER in UI → open scheduler tab → confirm read
    works, all write buttons disabled (already in MR !407), API-level
    write attempts (e.g. via curl with cookie) return 403.
  - Revoke a MANAGER session in CoreHub → within cache TTL the next
    chronos write still succeeds; **after** TTL, 401. Documented as
    expected.

---

## 11. Non-goals

- Per-pipeline / per-schedule ACLs. If VIEWER can read jobs at all,
  they can read all of them. Row-level auth is a separate story.
- Audit logging of who did what (worth adding later, out of scope).
- Renewing chronos's own module JWT on user actions.
- Replacing HMAC256 with RSA/JWK on CoreHub. Independent decision.

---

## 12. Effort estimate

- Iteration 1: ~½ day (module + tests, mostly boilerplate).
- Iteration 2: ~1 day (route wiring + integration tests + release notes).
- Iteration 3: ~1–2 days (audit + kotlin change + lockstep deploy).
- Iteration 4: ~½ day (metrics + hardening).

**Total: ~3–4 dev-days**, plus review + staging validation.

---

## Appendix A — Why not a middleware over every route

FastAPI's `Depends` is more idiomatic than a `BaseHTTPMiddleware`, lets
individual routes opt out (healthcheck, docs) without path-list
maintenance, and makes the guard testable in isolation. A middleware
would still be needed for cache-key normalisation and 401 rate-limiting
in Iteration 4 — but the guard itself belongs in `Depends`.

## Appendix B — Files this plan touches

New:
- `gluesync_scheduler/security/__init__.py`
- `gluesync_scheduler/security/user_role.py`
- `gluesync_scheduler/security/auth.py`
- `gluesync_scheduler/security/corehub_introspect.py`
- `gluesync_scheduler/security/exceptions.py`
- `tests/security/test_user_role.py`
- `tests/security/test_introspect.py`
- `tests/security/test_auth_dependencies.py`

Modified:
- `gluesync_scheduler/core/app.py` — none if introspector is lazy;
  optionally register a startup log line.
- `gluesync_scheduler/api/router.py` — add `Depends(...)` per §5.
- `gluesync_scheduler/api/settings_router.py` — add `Depends(...)`.
- `gluesync_scheduler/api/pipeline_router.py` — Iteration 3.
- `gluesync_scheduler/api/webhook_router.py` — Iteration 3.
- `requirements.txt` — no additions expected (httpx, PyJWT already
  present). If we adopt `cachetools`, add it here.
- `README.md` + `CI_SETUP.md` — document new env vars.
