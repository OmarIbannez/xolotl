# xolotl console

The staff console of the xolotl platform: sign in (workspace, email, password, TOTP), the forced
password change, and the admin areas. React 19, Vite, TypeScript (strict), Tailwind v4, shadcn/ui,
TanStack Query, React Router, react-hook-form + zod, i18next, and a typed API client
(openapi-typescript + openapi-fetch). Spanish (es-MX) is the default language, English the second.

## Develop

Start the API first (from the repository root):

```bash
docker compose up -d db
uv run xolotl db bootstrap && uv run xolotl db upgrade
uv run xolotl tenant create --slug acme --legal-name "Acme SA de CV" --admin-email admin@acme.mx
uv run xolotl serve            # http://localhost:8000
```

`tenant create` prints the admin's one-time password once. Then:

```bash
cd console
npm ci
npm run dev                    # http://localhost:5173, proxies /v1 to the API on :8000
```

Sign in at `/login` with workspace `acme`, the email and the one-time password. The console asks for
a new password (at least 12 characters) and lands on the home page, which shows the tenant and your
roles. Once TOTP is enabled for an account, the sign-in asks for its code on a second step.

If port 8000 is taken, start the API elsewhere (`xolotl serve --port 8010`) and point the proxy at it:
`API_PROXY_TARGET=http://localhost:8010 npm run dev`.

Node: the image and CI build with one exact version, 22.23.3 (`ARG NODE_VERSION` in the Dockerfile and
`node-version` in `.github/workflows/ci.yml`, kept equal by `tests/test_toolchain_pins.py`); jsdom, which the
component tests need, requires at least 22.22.2. To run the gates under it without installing it:
`npx -p node@22.23.3 npm run test`.

| Script | What it does |
| --- | --- |
| `npm run dev` | Vite dev server with the `/v1` proxy |
| `npm run build` | `tsc -b` then `vite build` into `dist/` |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run lint` | ESLint (no `any`) |
| `npm test` | Vitest: API client, idempotency, session store, routing rules, catalogues, formatting, and the components and pages (jsdom) |
| `npm run test:e2e` | Playwright smoke against a running stack (see "End-to-end smoke") |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from a running API |
| `npm run gen:api:file` | Regenerate it from `openapi.json` in this directory |

## Regenerate the API types

`src/api/schema.d.ts` is generated from the API's OpenAPI document and committed. With the API running:

```bash
npm run gen:api                # reads http://localhost:8000/v1/openapi.json
```

Without a database or a running server, export the document from the Python app and generate from
the file (`openapi.json` is git-ignored):

```bash
# from the repository root
uv run python -c "import json; from xolotl.app import build_app; print(json.dumps(build_app().openapi()))" > console/openapi.json
cd console && npm run gen:api:file
```

`src/api/types.ts` aliases the generated schemas (every route the console calls is in the OpenAPI
document) and adds the problem+json shapes, which the document does not describe.

## How it fits together

- **API client** (`src/api/client.ts`): one `openapi-fetch` client on `/v1`, so call sites write
  `api.GET('/auth/me')`. Middleware adds `Authorization: Bearer <token>`, adds an `Idempotency-Key` to
  every POST outside `/auth/*`, and turns any non-2xx answer into an `ApiError { status, code, detail,
  errors, requestId }` parsed from problem+json (a body that is not a problem becomes `HTTP_<status>`; a
  network failure becomes `NETWORK_ERROR`). A call that resolves has succeeded; use `unwrap` for the JSON
  body and `expectNoContent` for a 204.
- **Idempotency**: a call may bring its own key in `headers: { 'Idempotency-Key': key }`; without one,
  each request gets a new key. Anything a user submits (create, revoke, disable) goes through
  `useIdempotentMutation` (`src/api/use-idempotent-mutation.ts`, rules in `src/api/idempotency.ts`):
  one key per submission; the *same* key when the user submits the same values again after a failure
  that left the outcome unknown (no answer, a 5xx, a request still running), so the API replays the first
  answer instead of creating the thing twice; a *new* key after a success, after the API refused the
  request (any other 4xx) or when the values were edited; and a repeat while the request is in flight
  joins it instead of sending another. An answer with `Idempotent-Replay: true` is a success with the
  same body (`unwrapReplayable` reports it as `replayed`).
- **One-time secrets** (a new API key, an initial password): `OneTimeSecretDialog` shows the value once
  with a copy button, and cannot be closed (button, Escape, a click outside) until the user ticks "I
  saved it". The secret lives only in the props of that dialog: it is not written to storage or logs, and
  the mutations that return one use `gcTime: 0` so TanStack's mutation cache does not keep it. The form
  dialogs cannot be dismissed while their request is out, so an answer carrying a secret is never lost.
- **Lists** (`DataTable`, `CursorPager`, `DateTimeCell`): the API paginates by cursor and only gives the
  next one, so `CursorPager` keeps the back stack in its own state and starts over when the page moves
  to another cursor by itself (back to the first page after a create). Queries are keyed by tenant and
  cursor, and a mutation invalidates every page of its list.
- **Session** (`src/auth/session.ts`): the access token lives in memory and is mirrored to
  `sessionStorage`, never `localStorage`. A 401 clears it and the route guards send the visitor to
  `/login`; a 403 `PASSWORD_CHANGE_REQUIRED` sends them to `/setup-password`, and back afterwards.
  `src/auth/AuthProvider.tsx` loads `GET /auth/me` on boot and offers `login`, `loginTotp`, `logout`,
  `changePassword` and `refreshMe`. A password change ends every earlier session, this one included,
  and the API answers it with the token to continue with, which `changePassword` adopts.
- **Guards** (`src/auth/guards.tsx`, rules in `src/auth/access.ts`): `RequireAuth` for signed-in staff,
  `RequireRole(roles)` for admin areas. `ACCESS` maps each area to the roles that may open it; the
  navigation hides what the routes block. The API remains the authority.
- **i18n** (`src/i18n`): every user-visible string has a key in `es-MX.json` and `en.json`; the build
  fails when the two differ (`satisfies` in `index.ts`) or when a component uses a key that does not
  exist (typed `t`). Problem codes map to `errors.<CODE>` (`describeError`); form schemas carry
  `msgKey('required')`-style ids that `Field` translates, so messages follow the language switcher.
  Dates, numbers and country names go through `Intl` with the active locale (`useFormat`).
- **Design tokens** live in `src/index.css`; components use tokens only, and status colors always come
  with an icon and a label.
- **Served under a policy.** The API sends the console's pages (not its own `/v1`) with a
  `Content-Security-Policy` (`default-src 'self'`, scripts from the same origin only, no framing),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: same-origin`; they are
  defined in `src/xolotl/platform/api/static.py`. In practice: no inline script, no `eval`, no third-party
  origin (fonts, analytics, a CDN script) without changing the policy there and in the tests that pin it.
  `src/lib/zod-config.ts` keeps zod from probing for `eval`, which the policy blocks; it must stay the first
  import of `main.tsx`. The dev server (`npm run dev`) sends none of this, so the smoke
  (`npm run test:e2e`) is what catches a violation: it fails on any `securitypolicyviolation` the page reports.

## Tests

`npm test` runs Vitest. Logic tests (`*.test.ts`: the client, idempotency, session, routing rules,
catalogues) run in node. Component and page tests (`*.test.tsx`) start with `// @vitest-environment
jsdom` and use Testing Library. `src/test/` holds what they share: `setup.ts` (i18n in es-MX, cleanup),
and two ways to stand in for the API. `render.tsx` (`renderWithProviders`, `providers` for hooks) with
`fake-api.ts` replaces the typed client, so the real query hooks, mutations and pages run against answers
a test chooses:

```ts
vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))
```

`render-app.tsx` with `fake-network.ts` leaves the real client and its middleware in place and replaces only
the network under it (`installFakeApi({ 'GET /v1/auth/me': () => json(me) })`), so a test sees what the
browser would send and receive: it renders a page inside the real `AuthProvider` and a signed-in session.
`time-cells.ts` reads the `<time>` cells of the tables.

## End-to-end smoke

`npm run test:e2e` drives a real Chromium through the console against a running deployment:
sign in, the forced password change, turning TOTP on and signing in with a code, creating an API
key (shown once, listed masked), verifying the audit chain, assigning the ITF policy pack and reading
its tier limits in the pack viewer, and what happens when the server ends the session. It also checks that the API and the console come from one origin and that a deep link
such as `/audit` loads. The deployment is the one that ships: Postgres plus the application image,
which serves the console and the API.

```bash
npx playwright install chromium          # once
docker compose up -d --build --wait      # from the repository root; the console is built into the image
docker compose run --rm -T api xolotl tenant create --slug acme --legal-name "Acme SA de CV" --admin-email admin@acme.mx
docker compose run --rm -T api xolotl packs load --allow-unverified   # the pack viewer step needs MX/ITF-IFPE v1
docker compose run --rm -T api xolotl holidays seed
docker compose run --rm -T api xolotl units seed --allow-unverified
E2E_ADMIN_PASSWORD='<the one-time password it printed>' npm run test:e2e
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `E2E_ADMIN_PASSWORD` | required | The one-time password `tenant create` printed |
| `E2E_BASE_URL` | `http://127.0.0.1:8000` | Where the stack listens |
| `E2E_TENANT_SLUG` | `acme` | The tenant's slug |
| `E2E_TENANT_LEGAL_NAME` | `Acme SA de CV` | Its legal name, which the home page must show |
| `E2E_ADMIN_EMAIL` | `admin@acme.mx` | The first administrator |

The flow consumes the one-time password and changes the administrator's password, so every run
needs a new tenant; there are no retries for the same reason. The CI job `console` creates a tenant
the same way, runs the same three seed commands, and passes its password through `E2E_ADMIN_PASSWORD`. If the default ports are taken,
publish the API elsewhere (a compose override with `ports: !override ["127.0.0.1:8010:8000"]`) and
set `E2E_BASE_URL`. Every run, local or CI, writes the HTML report to `playwright-report/` (open it
with `npx playwright show-report playwright-report`; it is never opened for you), and a failed run
leaves its traces and screenshots in `test-results/`; both are git-ignored. Strings are read from
`src/i18n/es-MX.json`, so a copy change does not break the flow; TOTP codes are computed in
`e2e/totp.ts` (RFC 6238, with its own vector tests).

## Layout

```
src/
  api/          client.ts · errors.ts (ApiError) · idempotency.ts · use-idempotent-mutation.ts · queries.ts ·
                field-errors.ts · types.ts · schema.d.ts (generated)
  auth/         session.ts · AuthProvider.tsx · use-auth.ts · guards.tsx · access.ts · redirect.ts
  components/   ui/ (shadcn) · layout/ (AppShell, sidebar, header) · common/ (DataTable, CursorPager,
                OneTimeSecretDialog, ConfirmDialog, CheckboxGroup, badges, ...)
  features/     auth/ · home/ · api-keys/ · staff/ · audit/ · security/
                parties/ (the Perfil de comportamiento tab: BehaviourProfileCard, DeclaredProfileDialog) ·
                rails/ (Admin › Rieles, /admin/rails, ADMIN) · ...
  i18n/         index.ts · es-MX.json · en.json · errors.ts · validation.ts · use-locale.ts
  lib/          clipboard.ts · format.ts · utils.ts
  test/         setup.ts · render.tsx · fake-api.ts · fake-network.ts · render-app.tsx · time-cells.ts
```
