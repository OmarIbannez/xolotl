// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { RequireAuth } from '@/auth/guards'
import { session } from '@/auth/session'
import esMX from '@/i18n/es-MX.json'
import { json, noContent, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp, TEST_TOKEN } from '@/test/render-app'
import { SetupPasswordPage } from './SetupPasswordPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const es = esMX.auth.setupPassword
const ROTATED = 'rotated-token'

/** The routes the app has around the forced change: the page itself and what it hands back to. */
const router = (
  <Routes>
    <Route path="/login" element={<p>login page</p>} />
    <Route element={<RequireAuth allowPasswordChange />}>
      <Route path="/setup-password" element={<SetupPasswordPage />} />
    </Route>
    <Route element={<RequireAuth />}>
      <Route path="/" element={<p>home page</p>} />
      <Route path="/audit" element={<p>audit page</p>} />
    </Route>
  </Routes>
)

/**
 * An account that must change its password, on an API that remembers whether it has and,
 * when `rotates`, retires the token that made the change (the current contract).
 */
function mustChangeApi({ rotates }: { rotates: boolean }): FakeRoutes {
  let validToken = TEST_TOKEN
  let changed = false
  const me = makeMe({ must_change_password: true })
  return {
    'POST /v1/auth/password': () => {
      changed = true
      if (!rotates) return noContent()
      validToken = ROTATED
      return json({ access_token: ROTATED, token_type: 'Bearer', expires_in: 28800, must_change_password: false })
    },
    'GET /v1/auth/me': (request) =>
      request.headers.get('Authorization') === `Bearer ${validToken}`
        ? json({ ...me, must_change_password: !changed })
        : problem(401, 'UNAUTHENTICATED'),
  }
}

async function changePassword(user: ReturnType<typeof renderApp>['user']) {
  await user.type(await screen.findByLabelText(es.current), 'the-temporary-password')
  await user.type(screen.getByLabelText(es.new), 'a-brand-new-password')
  await user.type(screen.getByLabelText(es.confirm), 'a-brand-new-password')
  await user.click(screen.getByRole('button', { name: es.submit }))
}

describe('the forced password change', () => {
  it('lands the user back on the page they were headed for, signed in with the new token', async () => {
    const { user, fakeApi } = renderApp(router, {
      path: '/setup-password',
      state: { from: '/audit' },
      routes: mustChangeApi({ rotates: true }),
    })

    await changePassword(user)

    expect(await screen.findByText('audit page')).toBeInTheDocument()
    expect(session.getToken()).toBe(ROTATED)
    expect(session.getSnapshot().endedBy).toBeNull() // not bounced to the sign-in by a 401
    // Every call after the change used the new token: the old one was already dead.
    const sent = fakeApi.requests.map((request) => `${request.method} ${new URL(request.url).pathname} ${request.headers.get('Authorization')}`)
    const at = sent.findIndex((line) => line.startsWith('POST /v1/auth/password'))
    for (const line of sent.slice(at + 1)) expect(line).toMatch(new RegExp(`Bearer ${ROTATED}$`))
  })

  it('does the same against an API that still answers 204 and keeps the token', async () => {
    const { user } = renderApp(router, {
      path: '/setup-password',
      state: { from: '/audit' },
      routes: mustChangeApi({ rotates: false }),
    })

    await changePassword(user)

    expect(await screen.findByText('audit page')).toBeInTheDocument()
    expect(session.getToken()).toBe(TEST_TOKEN)
  })

  it('goes home when no page was being waited for', async () => {
    const { user } = renderApp(router, { path: '/setup-password', routes: mustChangeApi({ rotates: true }) })

    await changePassword(user)

    expect(await screen.findByText('home page')).toBeInTheDocument()
  })

  it('keeps the user on the page, with the reason, while the API refuses the change', async () => {
    const api = mustChangeApi({ rotates: true })
    const { user } = renderApp(router, {
      path: '/setup-password',
      state: { from: '/audit' },
      routes: { ...api, 'POST /v1/auth/password': () => problem(422, 'VALIDATION_FAILED') },
    })

    await changePassword(user)

    expect(await screen.findByRole('alert')).toHaveTextContent(es.rejected)
    expect(screen.queryByText('audit page')).toBeNull()
    expect(session.getToken()).toBe(TEST_TOKEN)
  })

  it('puts the cursor in the first field, and offers to sign out instead', async () => {
    const { user } = renderApp(router, { path: '/setup-password', routes: mustChangeApi({ rotates: true }) })

    expect(await screen.findByLabelText(es.current)).toHaveFocus()

    await user.click(screen.getByRole('button', { name: es.signOut }))

    await waitFor(() => expect(session.getToken()).toBeNull())
    expect(session.getSnapshot().endedBy).toBe('signedOut')
    expect(await screen.findByText('login page')).toBeInTheDocument()
  })
})
