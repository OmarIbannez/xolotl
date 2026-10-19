# SP-01d — Console Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the staff console for the kernel: a React single-page app forked from sentinel's frontend, Spanish-first with keyed strings, that signs a staff user in (password, TOTP, forced password change), manages API keys and staff, shows and verifies the audit chain, enrols TOTP, and ships inside the API's Docker image served at `/`.

**Architecture:** The console lives in `console/` (Vite, React 19, TypeScript strict, Tailwind v4, shadcn/ui, TanStack Query, react-router, react-hook-form + zod, i18next). It talks only to the `/v1` API through one `openapi-fetch` client whose middleware adds the bearer token and `Idempotency-Key`, and converts problem+json errors into a typed `ApiError`. Types are generated from the API's OpenAPI document (`npm run gen:api`). The API serves the built console from `STATIC_DIR` with a single-page fallback; the Docker image gains a Node build stage. Role gating in the UI mirrors the API (`ADMIN` manages keys and staff; `MLRO`, `ADMIN`, `AUDITOR_RO` read audit) but the API remains the authority.

**Tech Stack:** Node 22+ (25 in development), npm, Vite 7, React 19, TypeScript 5, Tailwind 4, shadcn/ui (radix-ui), @tanstack/react-query 5, react-router-dom 7, react-hook-form 7 + zod 4, openapi-fetch + openapi-typescript, i18next + react-i18next + i18next-browser-languagedetector, sonner, lucide-react, Playwright (smoke), eslint + typescript-eslint.

**Spec:** `docs/superpowers/specs/2026-10-18-sp01-platform-kernel-design.md` §11 (console shell), §6–§7 (API contract), §12–§13 (test matrix and exit criteria); program spec D20 (two API audiences, enums emitted once) and D23 (staff auth).

**Note on code in this plan:** the console is a fork of an existing, MIT-licensed codebase (`~/code/sentinel/frontend`), so the "code" for most components already exists as the template. Each task therefore specifies files, routes, state, API calls, i18n keys and acceptance commands exactly, and gives verbatim code only where the behaviour is new (API client middleware, session store, SPA serving). Implementers follow the fork's existing component conventions.

## Global Constraints

- Directory `console/` at the repository root; package name `xolotl-console`; scripts `dev`, `build` (`tsc -b && vite build`), `lint`, `typecheck`, `preview`, `gen:api` (`openapi-typescript http://localhost:8000/v1/openapi.json -o src/api/schema.d.ts --default-non-nullable false`), `gen:api:file` (same from `openapi.json`), `test:e2e` (Playwright).
- Every user-visible string is keyed in `src/i18n/es-MX.json` and `src/i18n/en.json`; `es-MX` is the default locale; no hard-coded copy in components.
- API conventions (spec §7): base `/v1`; `Authorization: Bearer <jwt>`; `Idempotency-Key` on every POST except `/v1/auth/*` (one UUID per mutation attempt, reused on retries of that attempt); errors are problem+json `{type,title,status,detail,instance,code,errors?}`; 401 clears the session; 403 `PASSWORD_CHANGE_REQUIRED` routes to `/setup-password`.
- Tokens live in memory, mirrored to `sessionStorage` only (never `localStorage`); nothing secret is logged to the console.
- Role gating: `ADMIN` → API keys, Staff; `MLRO`, `ADMIN`, `AUDITOR_RO` → Audit; everyone → Home, Security. Navigation hides what the role cannot open; routes also guard.
- One-time secrets (new API key, initial staff password) are shown once in a dialog with a copy button and never re-fetched or stored.
- Gates: `npm ci && npm run typecheck && npm run lint && npm run build` green; no `any`; Playwright smoke green against the compose stack in Task C4; backend gates (`make ci`) stay green when backend files change (Task C4 only).
- Commit after every task with the `Co-Authored-By`/`Claude-Session` trailers used on this branch.

## Review Focus

1. A 401 arriving mid-session (expired token, disabled user) must drop the session and land on `/login` without a redirect loop or a stale query cache showing the previous tenant's data. Test in C1 (unit: client middleware) and C4 (smoke).
2. A `403 PASSWORD_CHANGE_REQUIRED` on any page must route to `/setup-password` and, after success, back to the page the user wanted. Test in C1.
3. Creating an API key or a staff user twice by double-clicking must not create two: the same `Idempotency-Key` is reused for the retry of the same attempt and a new one is generated only for a new attempt. Test in C2.
4. The audit list must render `detail` JSON safely (no HTML injection, long values truncated with expand) and the verify result must distinguish "valid, 0 entries" from "broken at seq N". Test in C3.
5. The built console served by the API must deep-link: `GET /audit` on a fresh load returns `index.html` (SPA fallback) while `GET /v1/...` and `/healthz` are never shadowed. Test in C4.

## File structure

```
console/
  package.json · package-lock.json · vite.config.ts · tsconfig*.json · eslint.config.js · components.json · index.html · README.md
  openapi.json (git-ignored; exported for gen:api:file)
  src/
    main.tsx · App.tsx (router) · index.css
    i18n/{index.ts, es-MX.json, en.json}
    api/{client.ts, errors.ts, schema.d.ts (generated), types.ts (hand-written until generated), queries.ts}
    auth/{session.ts, SessionProvider.tsx, guards.tsx}
    components/ui/*  (shadcn, from the fork)
    components/layout/{AppShell.tsx, Sidebar.tsx, Header.tsx, LanguageSwitcher.tsx}
    components/common/{OneTimeSecretDialog.tsx, ProblemAlert.tsx, DataTable.tsx, CursorPager.tsx, RoleBadge.tsx, EnvBadge.tsx}
    features/auth/{LoginPage.tsx, TotpStep.tsx, SetupPasswordPage.tsx}
    features/home/HomePage.tsx
    features/api-keys/{ApiKeysPage.tsx, CreateApiKeyDialog.tsx}
    features/staff/{StaffPage.tsx, CreateStaffDialog.tsx}
    features/audit/{AuditPage.tsx, VerifyCard.tsx}
    features/security/SecurityPage.tsx (TOTP enrolment)
  e2e/{smoke.spec.ts, playwright.config.ts}
src/xolotl/platform/api/static.py   (C4: SPA mount)
Dockerfile                           (C4: console build stage)
.github/workflows/ci.yml             (C4: console job)
```

---

### Task C1: Fork, i18n scaffold, typed API client, auth flow, shell, placeholders

**Files:** everything under `console/` listed above except the feature pages of C2/C3 and `e2e/`; root `.gitignore` gains `console/node_modules/`, `console/dist/`, `console/openapi.json`.

**Interfaces produced:**
- `api/client.ts`: `export const api = createClient<paths>({ baseUrl: "/v1" })` with middleware `authMiddleware` (adds `Authorization` from `session.token`), `idempotencyMiddleware` (adds `Idempotency-Key: crypto.randomUUID()` to POST requests whose path does not start with `/auth/`; a caller may pass its own key via `headers`), `problemMiddleware` (on non-2xx throws `ApiError`).
- `api/errors.ts`: `class ApiError extends Error { status: number; code: string; detail: string; errors?: {loc: (string|number)[]; msg: string; type: string}[] }` plus `isApiError(e)`; `problemFromResponse(res): Promise<ApiError>` falling back to `code: "HTTP_<status>"` when the body is not a problem.
- `auth/session.ts`: `type Session = { token: string | null; me: Me | null }`; store with `login(slug,email,password) → {mfaRequired: true, mfaToken} | {ok: true}`, `loginTotp(mfaToken, code)`, `logout()`, `refreshMe()`, `changePassword(current, next)`; token mirrored to `sessionStorage["xolotl.token"]`; on boot, if a token exists, `GET /auth/me` restores `me` or clears the token. `changePassword` posts `/auth/password` and adopts the `access_token` of its `200` response before refreshing `me`: the change ends every earlier session, the token that made it included, so the old token gets 401 from then on (API change in the 1a final fix wave; C1 shipped expecting a 204, so C4 makes the switch).
- `auth/guards.tsx`: `RequireAuth` (redirects to `/login` with `from`), `RequireRole(roles)` (renders a `ProblemAlert` with `errors.ROLE_FORBIDDEN` when the role is missing), `RequirePasswordSet` (redirects to `/setup-password` when `me.must_change_password`).
- Routes: `/login`, `/setup-password`, `/` (Home), `/api-keys`, `/staff`, `/audit`, `/security` (the last four are placeholders in C1).
- i18n keys namespaces: `common.*`, `nav.*`, `auth.*`, `home.*`, `apiKeys.*`, `staff.*`, `audit.*`, `security.*`, `errors.<CODE>` with `errors.generic`.

**Steps** (the dispatch brief `console-task-1-brief.md` carries the detail used for the in-flight implementation):
- [ ] Fork with `rsync` excluding `node_modules`, `dist`, generated schema; delete sentinel features and branding; set package name and scripts; add i18n packages; `npm install`.
- [ ] Export the API's OpenAPI document (`uv run python -c "import json; from xolotl.app import build_app; print(json.dumps(build_app().openapi()))" > console/openapi.json`) and run `npm run gen:api:file`; hand-write `types.ts` for routes not yet merged, marked `// TODO(gen:api)`.
- [ ] Write `errors.ts`, `client.ts`, `session.ts`, `SessionProvider.tsx`, `guards.tsx`; unit-test the middleware with a mocked `fetch` (vitest is allowed if added as a dev dependency; otherwise a Playwright component check in C4): POST to `/api-keys` carries `Idempotency-Key`, POST to `/auth/login` does not, a 401 response clears the session, a problem body becomes `ApiError` with its `code`.
- [ ] Write the auth pages: `/login` (slug, email, password → TOTP step on `mfa_required`), `/setup-password` (current, new ≥ 12, confirm), redirects per Review Focus 2.
- [ ] Write `AppShell` (sidebar by role, header with tenant name/slug/env badge and user name/roles, language switcher, logout), `HomePage` (tenant card + roles), placeholders for the four pages.
- [ ] `npm run typecheck && npm run lint && npm run build`; walk the flow against a running API; commit `feat(console): fork the shell, i18n scaffold, typed API client and staff auth flow`.

---

### Task C2: API keys and Staff pages

**Files:** `features/api-keys/{ApiKeysPage.tsx, CreateApiKeyDialog.tsx}`, `features/staff/{StaffPage.tsx, CreateStaffDialog.tsx}`, `components/common/{DataTable.tsx, CursorPager.tsx, OneTimeSecretDialog.tsx}`, `api/queries.ts` additions, i18n keys.

**Interfaces:** `useApiKeys(cursor)`, `useCreateApiKey()`, `useRevokeApiKey()`, `useStaff(cursor)`, `useCreateStaff()`, `useDisableStaff()` as TanStack Query hooks; `CursorPager` with `nextCursor` and a back stack kept in component state; `OneTimeSecretDialog({title, secret, hint, onClose})` with copy-to-clipboard and an explicit "I saved it" acknowledgement.

- [ ] API keys page (ADMIN): table `name, key_prefix (monospace), scopes (badges), env, status, created_at, last_used_at`; "Nueva llave" dialog with `name`, `scopes` (multi-select from `tenants:read`, `tenants:write` plus free entry matching `^[a-z][a-z_]*:(read|write)$`; no preset for scopes no route honours yet, such as `audit:read`: the audit routes are staff-only), env fixed to the tenant's env; on 201 show `OneTimeSecretDialog` with the full key; "Revocar" with confirm; revoked rows muted. Errors: 409 shows `errors.CONFLICT` with the detail; 422 shows field errors from `errors[]`.
- [ ] Staff page (ADMIN): table `email, display_name, roles, status, must_change_password, created_at`; "Nuevo usuario" dialog (email, display name, roles multi-select); on 201 show the initial password once; "Deshabilitar" with confirm; your own row cannot be disabled (409 surfaced).
- [ ] Idempotency on retries (Review Focus 3): the create mutations generate one `Idempotency-Key` per dialog submission and pass it explicitly; a retry of the same submission reuses it; a successful replay (`Idempotent-Replay: true`) is treated as success.
- [ ] Unit tests for the key reuse logic and the pager; typecheck/lint/build; commit `feat(console): API keys and staff pages`.

---

### Task C3: Audit and Security pages

**Files:** `features/audit/{AuditPage.tsx, VerifyCard.tsx}`, `features/security/SecurityPage.tsx`, queries and i18n keys.

- [ ] Audit page (MLRO, ADMIN, AUDITOR_RO): table `seq, at (local time with zone), actor_type, actor_id, action, entity_type/entity_id, detail (JSON, truncated, expand in a sheet)`, hashes shown as short hex with copy; `action` filter input; cursor pager; "Verificar cadena" button rendering `VerifyCard` with `valid`, `entries_checked`, and `first_broken_seq`/`reason` when broken (Review Focus 4). `detail` is rendered as text, never as HTML.
- [ ] Security page (everyone): shows `totp_enabled`; "Activar TOTP" → `POST /auth/totp/enroll` → shows the `otpauth_uri` as a QR (a small QR component, e.g. `qrcode` package) plus the secret in monospace → code input → `POST /auth/totp/confirm` → success state; "Cambiar contraseña" form reusing the setup-password form component.
- [ ] Typecheck/lint/build; commit `feat(console): audit and security pages`.

---

### Task C4: Serve the console from the API, Docker build stage, Playwright smoke, CI

**Files:** `src/xolotl/platform/api/static.py` (new), `src/xolotl/platform/api/app.py` (mount), `tests/platform/test_static.py` (new), `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`, `console/e2e/*`, `console/package.json` (Playwright dev dependency and `test:e2e`).

**Backend code (verbatim):**

`src/xolotl/platform/api/static.py`:
```python
"""Serve the built console from STATIC_DIR with a single-page fallback."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

RESERVED_PREFIXES = ("/v1", "/healthz", "/readyz")


def mount_console(app: FastAPI, static_dir: Path) -> bool:
    """Mount the console when ``static_dir/index.html`` exists; return whether it did."""
    index = static_dir / "index.html"
    if not index.is_file():
        return False
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="console-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def console_index(full_path: str, request: Request) -> Response:
        if request.url.path.startswith(RESERVED_PREFIXES):
            return Response(status_code=404)
        candidate = static_dir / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    return True
```
`create_app` calls `mount_console(app, settings.static_dir)` after the routers (so `/v1` routes win), and `test_static.py` builds an app with a temporary `static_dir` holding `index.html` and `assets/app.js`, asserting: `GET /` and `GET /audit` return `index.html`; `GET /assets/app.js` returns the file; `GET /v1/openapi.json` and `GET /healthz` still work; `GET /../pyproject.toml` does not escape; with no `index.html` nothing is mounted and `GET /` is a 404 problem.

- [ ] Session: `changePassword` adopts the `access_token` returned by `POST /auth/password` (see C1's `auth/session.ts`); a unit test asserts the first request after the change carries the new token, and the Playwright smoke's forced password change lands on the home page without a second login; typecheck/lint/build.
- [ ] Backend: `static.py`, the mount, the tests; `make ci` green; commit `feat(api): serve the built console with a single-page fallback`.
- [ ] Dockerfile: add a `node:22-alpine` stage that runs `npm ci && npm run build` in `console/` and copies `console/dist` to `/app/src/xolotl/static` in the runtime stage (`XOLOTL_STATIC_DIR` defaults there); `.dockerignore` keeps `console/node_modules` out; `docker build` and `docker run ... xolotl --help`; `docker compose up` then `curl -s localhost:8000/` returns the console HTML and `/readyz` is ok.
- [ ] Playwright smoke (`console/e2e/smoke.spec.ts`) against the compose stack with a tenant created by `xolotl tenant create`: login → forced password change → home shows the tenant; enable TOTP in Security (read the secret from the page, compute a code with `otpauth` in the test) → logout → login with TOTP; create an API key, see it masked in the list; CI job `console` runs `npm ci`, `typecheck`, `lint`, `build`, and the smoke against `docker compose up` (Postgres + API).
- [ ] Commit `build: console in the image, Playwright smoke and CI job`.

---

## Exit criteria (spec §13, console rows)

`npm run typecheck`, `npm run lint`, `npm run build` green; the Docker image serves the console at `/`; a staff user signs in with TOTP in the console, creates a scoped API key and sees it masked; audit list and verify work for MLRO/ADMIN/AUDITOR_RO and are hidden for others; every string is keyed in es-MX and en.
