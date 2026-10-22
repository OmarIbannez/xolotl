import { expect, test } from '@playwright/test'
import es from '../src/i18n/es-MX.json' with { type: 'json' }
import { nextTotp, totp } from './totp'

/**
 * The smoke of the single-container deployment: the console and the API come from one
 * process, a tenant that `xolotl tenant create` has just made, and one pass through what the
 * kernel promises its first administrator. It only needs the stack to be up and the
 * administrator's one-time password (E2E_ADMIN_PASSWORD, printed by the CLI), which this
 * flow consumes: run it against a new tenant each time.
 *
 * Every label below comes from the console's own es-MX catalogue, so a copy change does
 * not break it and a renamed key fails the type check.
 */

const TENANT_SLUG = process.env.E2E_TENANT_SLUG ?? 'acme'
const LEGAL_NAME = process.env.E2E_TENANT_LEGAL_NAME ?? 'Acme SA de CV'
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? 'admin@acme.mx'
const ADMIN_NAME = 'Administrator' // what `xolotl tenant create` names the first administrator
const NEW_PASSWORD = 'correct-horse-battery-staple'
const ROTATED_PASSWORD = 'another-long-passphrase-1234'
const KEY_NAME = 'smoke key'
const KEY_PREFIX_LENGTH = 15 // `key_prefix` is the first 15 characters of the key
// What the library renders for CASH_STRUCTURING in es-MX (the backend's render golden).
const CASH_STRUCTURING_SENTENCE =
  'Dispara cuando el cliente recibe 3 o más depósitos en efectivo entre 70 % y 100 % del umbral en 7 días y su suma alcanza el umbral'
// The headers the API puts on the console's pages. Spelled out here on purpose: loosening
// the policy should be a visible change to a test, not only to the server.
const CONSOLE_HEADERS = {
  'content-security-policy':
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; " +
    "base-uri 'self'; form-action 'self'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'same-origin',
  'x-frame-options': 'DENY',
}

/** Fill the `{{name}}` placeholders of a catalogue string. */
function say(template: string, values: Record<string, string>): string {
  return template.replace(/\{\{(\w+)\}\}/g, (_, name: string) => values[name] ?? '')
}

function adminPassword(): string {
  const password = process.env.E2E_ADMIN_PASSWORD
  if (!password) {
    throw new Error(
      'E2E_ADMIN_PASSWORD is not set: pass the one-time password `xolotl tenant create` printed ' +
        '(see "End-to-end smoke" in console/README.md).',
    )
  }
  return password
}

test('a new tenant administrator signs in, secures the account and works in the console', async ({
  page,
  request,
}) => {
  const initialPassword = adminPassword()

  // Whatever the console throws, or fails to load from /assets, while it runs fails the smoke.
  const problems: string[] = []
  page.on('pageerror', (error) => problems.push(`uncaught: ${error.message}`))
  page.on('response', (response) => {
    // 304 is a revalidated asset (it carries an ETag), not a failure.
    if (response.url().includes('/assets/') && response.status() >= 400) {
      problems.push(`${response.status()} ${response.url()}`)
    }
  })
  // So does anything the Content-Security-Policy the API serves it with blocks. Chrome logs
  // a violation without telling page.on('console'), so the page reports its own, through a
  // binding that outlives the document (a variable in the page would not).
  await page.exposeFunction('reportCspViolation', (detail: string) => {
    problems.push(`blocked by the policy: ${detail}`)
  })
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
      const report = (window as unknown as { reportCspViolation: (detail: string) => void })
        .reportCspViolation
      report(`${event.violatedDirective} ${event.blockedURI} (${event.sourceFile}:${event.lineNumber})`)
    })
  })

  await test.step('one origin serves the console and the API', async () => {
    const health = await request.get('/healthz')
    expect(await health.json()).toEqual({ status: 'ok' })
    const ready = await request.get('/readyz')
    expect(await ready.json()).toEqual({ status: 'ok', database: 'ok' })
    const openapi = await request.get('/v1/openapi.json')
    expect(openapi.headers()['content-type']).toContain('application/json')
    // A deep link is the console's page, not a 404 and not the API's.
    const deepLink = await request.get('/audit', { headers: { Accept: 'text/html' } })
    expect(deepLink.headers()['content-type']).toContain('text/html')
    expect(await deepLink.text()).toContain('<div id="root"></div>')
    // The console's own pages say what the browser may do with them; the API's do not
    // (Swagger UI at /v1/docs loads its scripts from a CDN, which this policy would block).
    expect(deepLink.headers()).toMatchObject(CONSOLE_HEADERS)
    const docs = await request.get('/v1/docs')
    expect(docs.status()).toBe(200)
    expect(docs.headers()['content-security-policy']).toBeUndefined()
    // An unknown API path stays the API's answer.
    const unknown = await request.get('/v1/no-such-route')
    expect(unknown.status()).toBe(404)
    expect(unknown.headers()['content-type']).toBe('application/problem+json')
  })

  await test.step('a visitor with no session lands on the login page', async () => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: es.auth.login.title })).toBeVisible()
  })

  await test.step('the first sign in forces a new password', async () => {
    await page.getByLabel(es.auth.login.tenantSlug, { exact: true }).fill(TENANT_SLUG)
    await page.getByLabel(es.auth.login.email, { exact: true }).fill(ADMIN_EMAIL)
    await page.getByLabel(es.auth.login.password, { exact: true }).fill(initialPassword)
    await page.getByRole('button', { name: es.auth.login.submit, exact: true }).click()
    await expect(page).toHaveURL(/\/setup-password$/)
    await expect(page.getByRole('heading', { name: es.auth.setupPassword.title })).toBeVisible()
  })

  await test.step('the new password is accepted and the session carries on, with no second sign in', async () => {
    await page.getByLabel(es.auth.setupPassword.current, { exact: true }).fill(initialPassword)
    await page.getByLabel(es.auth.setupPassword.new, { exact: true }).fill(NEW_PASSWORD)
    await page.getByLabel(es.auth.setupPassword.confirm, { exact: true }).fill(NEW_PASSWORD)
    await page.getByRole('button', { name: es.auth.setupPassword.submit, exact: true }).click()

    // Home, on the token the change returned: the old one is already refused by the API.
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: es.home.title, level: 1 })).toBeVisible()
    await expect(page.getByText(say(es.home.welcome, { name: ADMIN_NAME }))).toBeVisible()
    const tenant = page.getByRole('region', { name: es.header.tenant })
    await expect(tenant).toContainText(LEGAL_NAME)
    await expect(tenant).toContainText(TENANT_SLUG)
    await expect(page.getByText(ADMIN_EMAIL, { exact: true })).toBeVisible()
  })

  const secret = await test.step('two-step verification is turned on from the Security page', async () => {
    await page.getByRole('link', { name: es.nav.security, exact: true }).click()
    await expect(page).toHaveURL(/\/security$/)
    await page.getByRole('button', { name: es.security.totp.enable, exact: true }).click()

    // The QR code is an inline SVG, so it draws under the policy (no img-src, no eval).
    await expect(page.getByRole('img', { name: es.security.totp.qrLabel })).toBeVisible()
    // The secret is on the page, once, for people who cannot scan the QR code.
    const shown = await page
      .getByRole('region', { name: es.security.totp.manualTitle })
      .locator('code')
      .innerText()
    const base32 = shown.replace(/\s+/g, '')
    expect(base32).toMatch(/^[A-Z2-7]{16,}$/)

    await page.getByLabel(es.security.totp.code, { exact: true }).fill(totp(base32))
    await page.getByRole('button', { name: es.security.totp.confirm, exact: true }).click()
    await expect(page.getByText(es.security.totp.successBody, { exact: true })).toBeVisible()
    return base32
  })

  await test.step('signing out ends the session', async () => {
    await page.getByTitle(es.header.accountMenu).click()
    await page.getByRole('menuitem', { name: es.header.signOut }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: es.auth.login.title })).toBeVisible()
  })

  await test.step('the next sign in asks for the code from the authenticator', async () => {
    await page.getByLabel(es.auth.login.tenantSlug, { exact: true }).fill(TENANT_SLUG)
    await page.getByLabel(es.auth.login.email, { exact: true }).fill(ADMIN_EMAIL)
    await page.getByLabel(es.auth.login.password, { exact: true }).fill(NEW_PASSWORD)
    await page.getByRole('button', { name: es.auth.login.submit, exact: true }).click()

    await expect(page.getByRole('heading', { name: es.auth.totp.title })).toBeVisible()
    // The next step's code, not the one the Security page just spent: a verifier may refuse
    // to see a code twice (RFC 6238 section 5.2), and the API accepts the next step.
    await page.getByLabel(es.auth.totp.code, { exact: true }).fill(nextTotp(secret))
    await page.getByRole('button', { name: es.auth.totp.submit, exact: true }).click()

    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByText(say(es.home.welcome, { name: ADMIN_NAME }))).toBeVisible()
  })

  const apiKey = await test.step('an API key is created, shown once and listed masked', async () => {
    await page.getByRole('link', { name: es.nav.apiKeys, exact: true }).click()
    await page.getByRole('button', { name: es.apiKeys.new, exact: true }).click()

    const form = page.getByRole('dialog', { name: es.apiKeys.create.title })
    await form.getByLabel(es.apiKeys.create.name, { exact: true }).fill(KEY_NAME)
    await form.getByRole('checkbox', { name: /tenants:read/ }).check()
    await form.getByRole('button', { name: es.apiKeys.create.submit, exact: true }).click()

    // The full key is on screen in this dialog and nowhere else.
    const shown = page.getByRole('dialog', { name: say(es.apiKeys.secret.title, { name: KEY_NAME }) })
    const key = await shown.locator('code').innerText()
    expect(key).toMatch(/^xk_sbx_[0-9a-f]{8}_[0-9a-f]{32}$/)
    await shown.getByRole('checkbox', { name: es.common.oneTime.acknowledge }).check()
    await shown.getByRole('button', { name: es.common.close, exact: true }).click()
    await expect(shown).toBeHidden()

    const row = page.getByRole('row', { name: new RegExp(KEY_NAME) })
    await expect(row).toContainText(key.slice(0, KEY_PREFIX_LENGTH))
    await expect(row).toContainText('tenants:read')
    await expect(row).toContainText(es.apiKeys.status.ACTIVE)
    // Masked: nothing past the prefix is rendered, and nothing is kept in the browser.
    await expect(page.locator('body')).not.toContainText(key.slice(KEY_PREFIX_LENGTH))
    return key
  })

  await test.step('neither the key nor the TOTP secret is kept in the browser', async () => {
    const stored = await page.evaluate(() => ({
      local: { ...window.localStorage },
      session: { ...window.sessionStorage },
    }))
    const everything = JSON.stringify(stored)
    expect(everything).not.toContain(apiKey)
    expect(everything).not.toContain(secret)
    // The token is in the tab's sessionStorage only, never in localStorage.
    expect(Object.keys(stored.local)).not.toContain('xolotl.token')
    expect(Object.keys(stored.session)).toContain('xolotl.token')
  })

  await test.step('a party is created, listed masked and its RFC revealed with an audit row', async () => {
    const RFC = 'CACX7605101P8'
    await page.getByRole('link', { name: es.nav.parties, exact: true }).click()
    await page.getByRole('button', { name: es.parties.new, exact: true }).click()
    await page.getByLabel(es.parties.create.givenNames, { exact: true }).fill('Ana María')
    await page.getByLabel(es.parties.create.firstSurname, { exact: true }).fill('Pérez')
    await page.getByLabel(es.parties.create.secondSurname, { exact: true }).fill('Gómez')
    await page.getByLabel(es.parties.create.birthDate, { exact: true }).fill('1976-05-10')
    // The identifier type defaults to RFC.
    await page.getByLabel(es.parties.create.identifierValue, { exact: true }).fill(RFC)
    await page.getByRole('button', { name: es.parties.create.submit, exact: true }).click()
    await expect(page.getByRole('heading', { name: es.parties.detail.title, level: 1 })).toBeVisible()
    await expect(page.locator('body')).toContainText('****01P8')
    await expect(page.locator('body')).not.toContainText(RFC)
    await page.getByRole('button', { name: es.parties.reveal.button, exact: true }).click()
    await expect(page.locator('body')).toContainText(RFC)
    await expect(page.locator('body')).toContainText(es.parties.reveal.notice)
    const token = await page.evaluate(() => window.sessionStorage.getItem('xolotl.token'))
    const audit = await page.request.get('/v1/audit?action=VIEW_PII', {
      headers: { Authorization: `Bearer ${token ?? ''}` },
    })
    expect(((await audit.json()) as { items: { action: string }[] }).items.length).toBeGreaterThan(0)
  })

  await test.step('the audit chain verifies, after a reload on a deep link', async () => {
    await page.goto('/audit') // a fresh load of a console route: the API serves the console's page
    await expect(page.getByRole('heading', { name: es.audit.title, level: 1 })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'api_key.created', exact: true })).toBeVisible()

    await page.getByRole('button', { name: es.audit.verify.button, exact: true }).click()
    const verdict = page.locator('section[data-outcome]')
    await expect(verdict).toHaveAttribute('data-outcome', 'valid')
    await expect(verdict).toContainText(es.audit.verify.valid.title)
    await expect(verdict).toContainText(es.audit.verify.entriesChecked)
  })

  await test.step('a wide audit table scrolls inside its card, not the page', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    const overflow = await page.getByRole('table').evaluate((table) => {
      const card = table.parentElement
      const root = document.documentElement
      return {
        page: root.scrollWidth - root.clientWidth,
        table: (card?.scrollWidth ?? 0) - (card?.clientWidth ?? 0),
      }
    })
    expect(overflow.page).toBe(0)
    expect(overflow.table).toBeGreaterThan(0)
    await page.setViewportSize({ width: 1280, height: 720 })
  })

  await test.step('the pack viewer shows the ITF tier limits', async () => {
    // The API assigns the pack (the console has no assignment form in SP-02a), on this
    // session's bearer: no second sign in and no second TOTP code.
    const token = await page.evaluate(() => window.sessionStorage.getItem('xolotl.token'))
    expect(token).toBeTruthy()
    const headers = { Authorization: `Bearer ${token}` }
    const packs = await request.get('/v1/policy/packs', { headers })
    expect(packs.ok()).toBe(true)
    const { items } = (await packs.json()) as { items: { id: string; code: string; version: number }[] }
    const itf = items.find((item) => item.code === 'MX/ITF-IFPE' && item.version === 1)
    expect(itf, 'the CI seeding loads MX/ITF-IFPE v1').toBeTruthy()
    const assigned = await request.post('/v1/policy/assignments', {
      headers: { ...headers, 'Idempotency-Key': 'e2e-assign-itf' },
      data: { pack_version_id: itf?.id, product: null, effective_from: '2026-01-01' },
    })
    expect(assigned.status()).toBe(201)

    await page.getByRole('link', { name: es.nav.policy, exact: true }).click()
    await expect(page).toHaveURL(/\/policy$/)
    // Today has no UDI row in the seeds: the missing-units notice shows and the limits still do.
    await expect(page.getByRole('cell', { name: '750 UDI', exact: true })).toBeVisible()
    await expect(page.getByRole('cell', { name: '1000 UDI', exact: true })).toBeVisible()
    // The header card and the assignments table both name the pack.
    await expect(page.getByText('MX/ITF-IFPE', { exact: true }).first()).toBeVisible()

    // Back to the audit page, where the next step refreshes.
    await page.getByRole('link', { name: es.nav.audit, exact: true }).click()
    await expect(page.getByRole('heading', { name: es.audit.title, level: 1 })).toBeVisible()
  })

  await test.step('an ad hoc check returns a status', async () => {
    // After the pack step: a run resolves the tenant's effective policy, which the pack
    // assignment provides. The smoke stack may have no lists loaded, so only the status is
    // asserted, never the hits.
    await page.getByRole('link', { name: es.nav.screeningCheck, exact: true }).click()
    await expect(page.getByRole('heading', { name: es.screening.check.title, level: 1 })).toBeVisible()
    await page.getByLabel(say(es.screening.check.name, { n: '1' }), { exact: true }).fill('Ana María Pérez Gómez')
    await page.getByRole('button', { name: es.screening.check.submit, exact: true }).click()
    const statuses = Object.values(es.screening.subjectStatus)
    await expect(page.getByText(new RegExp(`^(${statuses.join('|')})$`))).toBeVisible()

    // Back to the audit page, where the next step refreshes.
    await page.getByRole('link', { name: es.nav.audit, exact: true }).click()
    await expect(page.getByRole('heading', { name: es.audit.title, level: 1 })).toBeVisible()
  })

  await test.step('the rules library lists the Mexico typologies', async () => {
    // The image loads the typology library on `db upgrade` and `serve`, so every tenant sees it.
    await page.getByRole('link', { name: es.nav.monitoringRules, exact: true }).click()
    await expect(page.getByRole('heading', { name: es.rules.title, level: 1 })).toBeVisible()
    await expect(page.getByRole('tab', { name: es.rules.tabs.library })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByText('CASH_STRUCTURING', { exact: true })).toBeVisible()
    await expect(page.getByText(CASH_STRUCTURING_SENTENCE, { exact: true })).toBeVisible()

    // Back to the audit page, where the next step refreshes.
    await page.getByRole('link', { name: es.nav.audit, exact: true }).click()
    await expect(page.getByRole('heading', { name: es.audit.title, level: 1 })).toBeVisible()
  })

  await test.step('a session the server has ended sends the user back to the login page', async () => {
    // Another device changes the password with this session's token: from that moment the
    // token the console holds is refused, whatever the console does next.
    const token = await page.evaluate(() => window.sessionStorage.getItem('xolotl.token'))
    expect(token).toBeTruthy()
    const changed = await request.post('/v1/auth/password', {
      headers: { Authorization: `Bearer ${token}` },
      data: { current_password: NEW_PASSWORD, new_password: ROTATED_PASSWORD },
    })
    expect(changed.ok()).toBe(true)

    // The audit page asks the API again, and the API answers 401.
    await page.getByRole('button', { name: es.audit.filter.refresh, exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByText(es.auth.login.expired)).toBeVisible()
    // Nothing of the previous session stays on screen or in the tab.
    await expect(page.getByText(LEGAL_NAME)).toHaveCount(0)
    expect(await page.evaluate(() => window.sessionStorage.getItem('xolotl.token'))).toBeNull()
  })

  expect(problems).toEqual([])
})
