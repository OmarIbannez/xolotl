// @vitest-environment jsdom
import { render, screen, waitFor, within } from '@testing-library/react'
import { QRCodeSVG } from 'qrcode.react'
import { Route, Routes } from 'react-router-dom'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role } from '@/api/types'
import { RequireAuth } from '@/auth/guards'
import { session } from '@/auth/session'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { json, noContent, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp, TEST_TOKEN, type RenderAppOptions } from '@/test/render-app'
import { QR } from './qr'
import { SecurityPage } from './SecurityPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const es = esMX.security
const v = esMX.validation

// Like pyotp.random_base32(): 32 base32 characters.
const SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'
const SECRET_GROUPED = 'JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP'
const uriFor = (secret: string) =>
  `otpauth://totp/xolotl%3Aacme:admin%40acme.mx?secret=${secret}&issuer=xolotl%3Aacme`
const GOOD_CODE = '123456'

interface SecurityApiOptions {
  /** Whether TOTP is on when the page loads. */
  enabled?: boolean
  /** The secrets successive enrolments hand out (the last one repeats). */
  secrets?: string[]
}

/** The API's TOTP side: enrolment, confirmation with one good code, and the profile that follows. */
function securityApi({ enabled = false, secrets = [SECRET] }: SecurityApiOptions = {}) {
  const state = { enabled, enrolments: 0 }
  const enroll = () => {
    const secret = secrets[Math.min(state.enrolments, secrets.length - 1)] ?? SECRET
    state.enrolments += 1
    return json({ secret, otpauth_uri: uriFor(secret) })
  }
  const routes: FakeRoutes = {
    'GET /v1/auth/me': () => json(makeMe({ totp_enabled: state.enabled })),
    'POST /v1/auth/totp/enroll': enroll,
    'POST /v1/auth/totp/confirm': async (request) => {
      const body: unknown = await request.json()
      const code = typeof body === 'object' && body !== null && 'code' in body ? body.code : undefined
      if (code !== GOOD_CODE) return problem(422, 'VALIDATION_FAILED')
      state.enabled = true
      return noContent()
    },
  }
  return { state, routes, enroll }
}

/** The page the way the app mounts it: behind the sign-in guard only, open to any role. */
function renderSecurity(options: RenderAppOptions = {}) {
  return renderApp(
    <Routes>
      <Route element={<RequireAuth />}>
        <Route path="/security" element={<SecurityPage />} />
      </Route>
    </Routes>,
    { ...options, path: '/security' },
  )
}

const enable = () => screen.findByRole('button', { name: es.totp.enable })

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('SecurityPage: two-step verification', () => {
  it('says TOTP is off and offers to turn it on', async () => {
    const { routes } = securityApi()
    const { fakeApi } = renderSecurity({ routes })

    expect(await enable()).toBeEnabled()
    const card = screen.getByRole('heading', { level: 2, name: es.totp.title }).closest('[data-slot=card]') as HTMLElement
    expect(within(card).getByText(es.totp.off)).toBeInTheDocument()
    expect(within(card).getByText(es.totp.offBody)).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull() // no QR code, no secret, until asked for
    expect(fakeApi.calls('POST /v1/auth/totp/enroll')).toHaveLength(0)
  })

  it('shows a confirmation badge when it is on, with no way to turn it off', async () => {
    const { routes } = securityApi({ enabled: true })
    const { fakeApi } = renderSecurity({ routes })

    const card = (await screen.findByRole('heading', { level: 2, name: es.totp.title })).closest('[data-slot=card]') as HTMLElement
    expect(within(card).getByText(es.totp.on)).toBeInTheDocument()
    expect(within(card).getByText(es.totp.onBody)).toBeInTheDocument()
    expect(within(card).queryByRole('button')).toBeNull()
    expect(fakeApi.calls('POST /v1/auth/totp/enroll')).toHaveLength(0)
  })

  it('asks the API to enrol, then shows the QR code, the secret, a warning and the code field', async () => {
    const { routes } = securityApi()
    const { user, fakeApi } = renderSecurity({ routes })

    await user.click(await enable())

    expect(await screen.findByRole('img', { name: es.totp.qrLabel })).toBeInTheDocument()
    expect(screen.getByText(SECRET_GROUPED)).toBeInTheDocument()
    expect(screen.getByText(es.totp.onceTitle)).toBeInTheDocument()
    expect(screen.getByText(es.totp.onceBody)).toBeInTheDocument()
    expect(screen.getByLabelText(es.totp.code)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.totp.confirm })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: es.totp.enable })).toBeNull()

    const [request] = fakeApi.calls('POST /v1/auth/totp/enroll')
    expect(request?.headers.get('Authorization')).toBe(`Bearer ${TEST_TOKEN}`)
    expect(request?.headers.has('Idempotency-Key')).toBe(false) // /auth routes take none
    expect(await request?.text()).toBe('')
  })

  it('draws the QR code from the otpauth address, and the address itself is not printed', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })

    await user.click(await enable())

    const drawn = await screen.findByRole('img', { name: es.totp.qrLabel })
    const { container } = render(<QRCodeSVG value={uriFor(SECRET)} {...QR} />)
    const modules = (svg: Element | null) => svg?.querySelectorAll('path')[1]?.getAttribute('d')
    expect(modules(drawn)).toBeTruthy()
    expect(modules(drawn)).toBe(modules(container.querySelector('svg'))) // same data, same code
    expect(document.body.textContent).not.toContain('otpauth://')
  })

  it('focuses the first step, so the keyboard and screen readers start in the new panel', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })

    await user.click(await enable())

    expect(await screen.findByRole('heading', { name: es.totp.scanTitle })).toHaveFocus()
  })

  it('copies the secret without the spacing it is shown with', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })
    await user.click(await enable())

    await user.click(await screen.findByRole('button', { name: es.totp.copySecret }))

    expect(await navigator.clipboard.readText()).toBe(SECRET)
    expect(toast.success).toHaveBeenCalledWith(es.totp.secretCopied)
  })

  it('keeps the secret out of storage', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })

    await user.click(await enable())
    await screen.findByText(SECRET_GROUPED)

    for (const storage of [window.localStorage, window.sessionStorage]) {
      expect(JSON.stringify({ ...storage })).not.toContain(SECRET)
    }
  })

  it('turns TOTP on with a good code: success state, secret gone, profile refreshed', async () => {
    const { routes } = securityApi()
    const { user, fakeApi, queryClient } = renderSecurity({ routes })
    await user.click(await enable())

    await user.type(await screen.findByLabelText(es.totp.code), GOOD_CODE)
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))

    // Success: the badge, the message (which holds the focus) and a toast.
    const status = await screen.findByRole('status')
    expect(within(status).getByText(es.totp.success)).toBeInTheDocument()
    expect(status).toHaveFocus()
    expect(screen.getByText(es.totp.on)).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith(es.totp.success)
    // The call was a plain POST of the code.
    const [confirmRequest] = fakeApi.calls('POST /v1/auth/totp/confirm')
    expect(await confirmRequest?.json()).toEqual({ code: GOOD_CODE })
    expect(confirmRequest?.headers.has('Idempotency-Key')).toBe(false)
    // The profile was fetched again, which is where the badge comes from.
    await waitFor(() => expect(fakeApi.calls('GET /v1/auth/me').length).toBeGreaterThan(1))
    // And the secret is gone: from the page, and from what the app keeps in memory.
    expect(screen.queryByRole('img', { name: es.totp.qrLabel })).toBeNull()
    expect(document.body.textContent).not.toContain(SECRET)
    expect(document.body.textContent).not.toContain(SECRET_GROUPED)
    await waitFor(() => {
      const held = queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => JSON.stringify(mutation.state.data ?? null))
        .join()
      expect(held).not.toContain(SECRET)
    })
    expect(screen.queryByRole('button', { name: es.totp.enable })).toBeNull()
  })

  it('says so inside the form when the code is wrong, and keeps the secret for another try', async () => {
    const { routes } = securityApi()
    const { user, fakeApi } = renderSecurity({ routes })
    await user.click(await enable())
    const code = await screen.findByLabelText(es.totp.code)

    await user.type(code, '000000')
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))

    expect(await screen.findByText(es.totp.codeInvalid)).toBeInTheDocument()
    expect(code).toHaveAttribute('aria-invalid', 'true')
    expect(code).toHaveValue('') // start over
    expect(code).toHaveFocus()
    expect(screen.getByText(SECRET_GROUPED)).toBeInTheDocument() // the same secret, still on screen
    expect(toast.success).not.toHaveBeenCalled()
    expect(fakeApi.calls('POST /v1/auth/totp/enroll')).toHaveLength(1) // no second enrolment behind its back

    await user.type(code, GOOD_CODE)
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))

    expect(await screen.findByText(es.totp.success)).toBeInTheDocument()
  })

  it.each(['12345', 'abcdef', '123 456', '123456789'])(
    'does not send "%s", which is not a 6 to 8 digit code',
    async (typed) => {
      const { routes } = securityApi()
      const { user, fakeApi } = renderSecurity({ routes })
      await user.click(await enable())

      await user.type(await screen.findByLabelText(es.totp.code), typed)
      await user.click(screen.getByRole('button', { name: es.totp.confirm }))

      // (maxlength cuts the nine digits to eight, which is a valid length: it is sent.)
      if (typed === '123456789') {
        await waitFor(() => expect(fakeApi.calls('POST /v1/auth/totp/confirm')).toHaveLength(1))
        expect(await fakeApi.calls('POST /v1/auth/totp/confirm')[0]?.json()).toEqual({ code: '12345678' })
      } else {
        expect(await screen.findByText(v.totpCode)).toBeInTheDocument()
        expect(fakeApi.calls('POST /v1/auth/totp/confirm')).toHaveLength(0)
      }
    },
  )

  it('lets the keyboard user type a code the way a phone offers it', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })
    await user.click(await enable())

    const code = await screen.findByLabelText(es.totp.code)

    expect(code).toHaveAttribute('inputmode', 'numeric')
    expect(code).toHaveAttribute('autocomplete', 'one-time-code')
    expect(code).toHaveAttribute('maxlength', '8')
  })

  it('does not keep a spent code in memory, the wrong one or the good one', async () => {
    const { routes } = securityApi()
    const { user, queryClient } = renderSecurity({ routes })
    await user.click(await enable())
    const code = await screen.findByLabelText(es.totp.code)

    await user.type(code, '000000')
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))
    await screen.findByText(es.totp.codeInvalid)
    await user.type(code, GOOD_CODE)
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))
    await screen.findByText(es.totp.success)

    // Each code travelled as the confirm mutation's variables: neither may outlive its use.
    await waitFor(() => {
      const kept = queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => String(mutation.state.variables))
      expect(kept).not.toContain('000000')
      expect(kept).not.toContain(GOOD_CODE)
    })
  })

  it('forgets the secret when the user cancels, and starts a fresh one next time', async () => {
    const other = 'MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43U'
    const { routes } = securityApi({ secrets: [SECRET, other] })
    const { user, fakeApi, queryClient } = renderSecurity({ routes })
    await user.click(await enable())
    await screen.findByText(SECRET_GROUPED)

    await user.click(screen.getByRole('button', { name: es.totp.cancel }))

    expect(screen.queryByText(SECRET_GROUPED)).toBeNull()
    expect(screen.queryByRole('img', { name: es.totp.qrLabel })).toBeNull()
    await waitFor(() => {
      const held = queryClient.getMutationCache().getAll().map((m) => JSON.stringify(m.state.data ?? null)).join()
      expect(held).not.toContain(SECRET)
    })

    await user.click(await enable())

    expect(await screen.findByText('MFRG GZDF MZTW Q2LK NNWG 23TP OBYX E43U')).toBeInTheDocument()
    expect(screen.queryByText(SECRET_GROUPED)).toBeNull()
    expect(fakeApi.calls('POST /v1/auth/totp/enroll')).toHaveLength(2)
  })

  it('shows a failure to enrol and lets the user try again', async () => {
    const { routes, enroll } = securityApi()
    let failing = true
    const { user } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/totp/enroll': () => (failing ? problem(500, 'INTERNAL') : enroll()),
      },
    })

    await user.click(await enable())

    expect(await screen.findByRole('alert')).toHaveTextContent(esMX.errors.INTERNAL)
    failing = false
    await user.click(screen.getByRole('button', { name: es.totp.enable }))
    expect(await screen.findByText(SECRET_GROUPED)).toBeInTheDocument()
    expect(screen.queryByText(esMX.errors.INTERNAL)).toBeNull()
  })

  it('holds the button while the secret is being made', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const { routes, enroll } = securityApi()
    const { user, fakeApi } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/totp/enroll': async () => {
          await held
          return enroll()
        },
      },
    })

    await user.click(await enable())
    const busy = await screen.findByRole('button', { name: es.totp.enabling })
    expect(busy).toBeDisabled()
    await user.click(busy)
    release()

    await screen.findByText(SECRET_GROUPED)
    expect(fakeApi.calls('POST /v1/auth/totp/enroll')).toHaveLength(1)
  })

  it('catches up when TOTP was turned on elsewhere: says so and shows it on', async () => {
    const { state, routes } = securityApi()
    const { user } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/totp/enroll': () => {
          state.enabled = true // another tab got there first
          return problem(409, 'CONFLICT')
        },
      },
    })

    await user.click(await enable())

    expect(await screen.findByText(es.totp.alreadyEnabled)).toBeInTheDocument()
    expect(await screen.findByText(es.totp.on)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: es.totp.enable })).toBeNull()
  })

  it('catches up the same way when confirming finds it already on', async () => {
    const { state, routes } = securityApi()
    const { user } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/totp/confirm': () => {
          state.enabled = true
          return problem(409, 'CONFLICT')
        },
      },
    })
    await user.click(await enable())

    await user.type(await screen.findByLabelText(es.totp.code), GOOD_CODE)
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))

    expect(await screen.findByText(es.totp.alreadyEnabled)).toBeInTheDocument()
    expect(await screen.findByText(es.totp.on)).toBeInTheDocument()
    expect(screen.queryByText(SECRET_GROUPED)).toBeNull()
  })

  it('lets go of the secret when TOTP turns out to be on from elsewhere', async () => {
    const { state, routes } = securityApi()
    const { user, queryClient } = renderSecurity({
      routes: { ...routes, 'POST /v1/auth/password': () => noContent() },
    })
    await user.click(await enable())
    await screen.findByText(SECRET_GROUPED)

    // Another tab turns TOTP on; this page learns of it when the profile is next fetched
    // (here, after a password change from the card below).
    state.enabled = true
    const setup = esMX.auth.setupPassword
    await user.type(screen.getByLabelText(setup.current), 'the-old-password-1')
    await user.type(screen.getByLabelText(setup.new), 'a-brand-new-password')
    await user.type(screen.getByLabelText(setup.confirm), 'a-brand-new-password')
    await user.click(screen.getByRole('button', { name: setup.submit }))

    expect(await screen.findByText(es.totp.on)).toBeInTheDocument()
    expect(screen.queryByText(SECRET_GROUPED)).toBeNull()
    await waitFor(() => {
      const held = queryClient.getMutationCache().getAll().map((m) => JSON.stringify(m.state.data ?? null)).join()
      expect(held).not.toContain(SECRET)
    })
  })

  it('reports another failure to confirm and keeps the form', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({
      routes: { ...routes, 'POST /v1/auth/totp/confirm': () => problem(500, 'INTERNAL') },
    })
    await user.click(await enable())

    await user.type(await screen.findByLabelText(es.totp.code), GOOD_CODE)
    await user.click(screen.getByRole('button', { name: es.totp.confirm }))

    expect(await screen.findByText(esMX.errors.INTERNAL)).toBeInTheDocument()
    expect(screen.getByText(SECRET_GROUPED)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.totp.confirm })).toBeEnabled()
  })

  it('follows the language switcher', async () => {
    await i18n.changeLanguage('en')
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })

    await user.click(await screen.findByRole('button', { name: en.security.totp.enable }))

    expect(await screen.findByRole('img', { name: en.security.totp.qrLabel })).toBeInTheDocument()
    expect(screen.getByText(en.security.totp.onceTitle)).toBeInTheDocument()
  })
})

describe('SecurityPage: where the focus goes when the enrolment panel goes away', () => {
  /** The notice that tells the user TOTP was already on. */
  const notice = () => screen.getByText(es.totp.alreadyEnabled).closest('[role="alert"]')

  it('goes back to the button that opened it when the user cancels', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({ routes })
    await user.click(await enable())
    await screen.findByText(SECRET_GROUPED)

    await user.click(screen.getByRole('button', { name: es.totp.cancel }))

    await waitFor(() => expect(screen.getByRole('button', { name: es.totp.enable })).toHaveFocus())
  })

  it('moves to the notice when confirming finds TOTP already on, and stays there as the profile refreshes', async () => {
    const { state, routes } = securityApi()
    const { user } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/totp/confirm': () => {
          state.enabled = true
          return problem(409, 'CONFLICT')
        },
      },
    })
    await user.click(await enable())
    await user.type(await screen.findByLabelText(es.totp.code), GOOD_CODE)

    await user.click(screen.getByRole('button', { name: es.totp.confirm }))

    await screen.findByText(es.totp.on) // the refreshed profile has arrived and the Activar button is gone
    expect(notice()).toHaveFocus()
  })

  it('moves to the notice when enrolling finds TOTP already on', async () => {
    const { state, routes } = securityApi()
    const { user } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/totp/enroll': () => {
          state.enabled = true
          return problem(409, 'CONFLICT')
        },
      },
    })

    await user.click(await enable())

    await screen.findByText(es.totp.on)
    expect(notice()).toHaveFocus()
  })
})

describe('SecurityPage: changing the password', () => {
  const tokenResponse = (token: string) => ({
    access_token: token,
    token_type: 'Bearer',
    expires_in: 28800,
    must_change_password: false,
  })

  it('has the password form in its own card', async () => {
    const { routes } = securityApi()
    renderSecurity({ routes })

    const heading = await screen.findByRole('heading', { level: 2, name: es.password.title })
    const card = heading.closest('[data-slot=card]') as HTMLElement
    expect(within(card).getByLabelText(esMX.auth.setupPassword.current)).toBeInTheDocument()
    expect(within(card).getByLabelText(esMX.auth.setupPassword.new)).toBeInTheDocument()
    expect(within(card).getByLabelText(esMX.auth.setupPassword.confirm)).toBeInTheDocument()
    expect(within(card).getByLabelText(esMX.auth.setupPassword.current)).not.toHaveFocus() // the page does not grab focus
  })

  it('changes it, signs in with the token the API returns, and keeps the user on the page', async () => {
    const { routes } = securityApi()
    let valid = TEST_TOKEN
    const { user, fakeApi } = renderSecurity({
      routes: {
        ...routes,
        'POST /v1/auth/password': () => {
          valid = 'rotated-token'
          return json(tokenResponse('rotated-token'))
        },
        'GET /v1/auth/me': (request) =>
          request.headers.get('Authorization') === `Bearer ${valid}`
            ? json(makeMe())
            : problem(401, 'UNAUTHENTICATED'),
      },
    })
    const setup = esMX.auth.setupPassword

    await user.type(await screen.findByLabelText(setup.current), 'the-old-password-1')
    await user.type(screen.getByLabelText(setup.new), 'a-brand-new-password')
    await user.type(screen.getByLabelText(setup.confirm), 'a-brand-new-password')
    await user.click(screen.getByRole('button', { name: setup.submit }))

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(setup.success))
    expect(session.getToken()).toBe('rotated-token')
    expect(session.getSnapshot().endedBy).toBeNull()
    expect(screen.getByLabelText(setup.current)).toHaveValue('') // cleared
    expect(screen.getByLabelText(setup.new)).toHaveValue('')
    expect(await screen.findByRole('heading', { level: 1, name: es.title })).toBeInTheDocument() // still here
    const [request] = fakeApi.calls('POST /v1/auth/password')
    expect(await request?.json()).toEqual({
      current_password: 'the-old-password-1',
      new_password: 'a-brand-new-password',
    })
    // Nothing was sent with the retired token after the change.
    await waitFor(() => expect(fakeApi.calls('GET /v1/auth/me').length).toBeGreaterThan(1))
    expect(fakeApi.calls('GET /v1/auth/me').at(-1)?.headers.get('Authorization')).toBe('Bearer rotated-token')
  })

  it('shows a refused change inside the card (422) and leaves the session alone', async () => {
    const { routes } = securityApi()
    const { user } = renderSecurity({
      routes: { ...routes, 'POST /v1/auth/password': () => problem(422, 'VALIDATION_FAILED') },
    })
    const setup = esMX.auth.setupPassword

    await user.type(await screen.findByLabelText(setup.current), 'not-the-old-password')
    await user.type(screen.getByLabelText(setup.new), 'a-brand-new-password')
    await user.type(screen.getByLabelText(setup.confirm), 'a-brand-new-password')
    await user.click(screen.getByRole('button', { name: setup.submit }))

    const card = screen.getByRole('heading', { level: 2, name: es.password.title }).closest('[data-slot=card]') as HTMLElement
    expect(await within(card).findByRole('alert')).toHaveTextContent(setup.rejected)
    expect(session.getToken()).toBe(TEST_TOKEN)
    expect(toast.success).not.toHaveBeenCalled()
  })
})

describe('SecurityPage: who may open it', () => {
  it('has a heading, a description and the two cards', async () => {
    const { routes } = securityApi()
    renderSecurity({ routes })

    expect(await screen.findByRole('heading', { level: 1, name: es.title })).toBeInTheDocument()
    expect(screen.getByText(es.description)).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      es.totp.title,
      es.password.title,
    ])
    expect(document.title).toContain(es.title)
  })

  it.each<Role>(['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN'])(
    'is open to %s: any signed-in staff may secure their own account',
    async (role) => {
      const { routes } = securityApi()
      renderSecurity({
        routes: { ...routes, 'GET /v1/auth/me': () => json(makeMe({ roles: [role] })) },
      })

      expect(await enable()).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: es.password.title })).toBeInTheDocument()
    },
  )
})
