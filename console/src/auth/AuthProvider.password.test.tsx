// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import type { Me } from '@/api/types'
import { json, noContent, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp, TEST_TOKEN } from '@/test/render-app'
import { session } from './session'
import { useAuth } from './use-auth'

const STORAGE_KEY = 'xolotl.token'
const NEW_TOKEN = 'rotated-token'

/** What the password endpoint answers once the contract is `200 TokenResponse`. */
const tokenResponse = (token: string) => ({
  access_token: token,
  token_type: 'Bearer',
  expires_in: 28800,
  must_change_password: false,
})

/** A button that changes the password through the provider, and what the provider then says. */
function ChangePassword() {
  const { changePassword, user, mustChangePassword } = useAuth()
  const [outcome, setOutcome] = useState('not yet')
  return (
    <>
      <button
        onClick={() => {
          changePassword({ currentPassword: 'old-password-123', newPassword: 'new-password-456' }).then(
            () => setOutcome('changed'),
            (error: unknown) => setOutcome(`failed ${error instanceof Error ? error.name : 'unknown'}`),
          )
        }}
      >
        change
      </button>
      <p>outcome: {outcome}</p>
      <p>user: {user?.display_name ?? 'nobody'}</p>
      <p>gate: {mustChangePassword ? 'closed' : 'open'}</p>
    </>
  )
}

/**
 * An API that retires the token it was called with when the password changes, as the
 * contract now says: afterwards only `NEW_TOKEN` is accepted. `me` is what GET /auth/me
 * answers for a valid token.
 */
function rotatingApi(me: Me, { answer }: { answer: () => Response }): FakeRoutes {
  let valid = TEST_TOKEN
  return {
    'POST /v1/auth/password': () => {
      valid = NEW_TOKEN
      return answer()
    },
    'GET /v1/auth/me': (request) =>
      request.headers.get('Authorization') === `Bearer ${valid}` ? json(me) : problem(401, 'UNAUTHENTICATED'),
  }
}

describe('AuthProvider.changePassword and the replacement token', () => {
  it('adopts the token a 200 answer carries, in memory and in sessionStorage', async () => {
    const { user, queryClient } = renderApp(<ChangePassword />, {
      routes: rotatingApi(makeMe(), { answer: () => json(tokenResponse(NEW_TOKEN)) }),
    })
    await screen.findByText('user: Ana Admin')

    await user.click(screen.getByRole('button', { name: 'change' }))

    expect(await screen.findByText('outcome: changed')).toBeInTheDocument()
    expect(session.getToken()).toBe(NEW_TOKEN)
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe(NEW_TOKEN)
    expect(session.getSnapshot().endedBy).toBeNull()
    // The profile moved to the new token's key and the retired token's entry is gone.
    expect(queryClient.getQueryData(['me', TEST_TOKEN])).toBeUndefined()
    expect(queryClient.getQueryData(['me', NEW_TOKEN])).toMatchObject({ display_name: 'Ana Admin' })
  })

  it('sends nothing after the change with the retired token', async () => {
    const { user, fakeApi } = renderApp(<ChangePassword />, {
      routes: rotatingApi(makeMe(), { answer: () => json(tokenResponse(NEW_TOKEN)) }),
    })
    await screen.findByText('user: Ana Admin')

    await user.click(screen.getByRole('button', { name: 'change' }))
    await screen.findByText('outcome: changed')
    await waitFor(() => expect(fakeApi.calls('GET /v1/auth/me').length).toBeGreaterThan(1)) // the confirming refresh

    const sent = fakeApi.requests.map((request) => `${request.method} ${new URL(request.url).pathname} ${request.headers.get('Authorization')}`)
    const at = sent.indexOf(`POST /v1/auth/password Bearer ${TEST_TOKEN}`)
    expect(at).toBeGreaterThanOrEqual(0) // the change itself was made with the token then current
    expect(sent.slice(at + 1).length).toBeGreaterThan(0)
    for (const request of sent.slice(at + 1)) expect(request).toMatch(new RegExp(`Bearer ${NEW_TOKEN}$`))
  })

  it('stays signed in, with the profile on screen, while the new token is confirmed', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const me = makeMe({ must_change_password: true })
    let confirming = false
    const { user } = renderApp(<ChangePassword />, {
      me,
      routes: {
        'POST /v1/auth/password': () => json(tokenResponse(NEW_TOKEN)),
        'GET /v1/auth/me': async (request) => {
          if (request.headers.get('Authorization') === `Bearer ${NEW_TOKEN}`) {
            confirming = true
            await held // the confirmation takes a while
            return json({ ...me, must_change_password: false })
          }
          return json(me)
        },
      },
    })
    await screen.findByText('gate: closed') // the account must still change its password

    await user.click(screen.getByRole('button', { name: 'change' }))

    // The gate is lifted and the user still known before the confirmation has answered.
    expect(await screen.findByText('gate: open')).toBeInTheDocument()
    expect(screen.getByText('user: Ana Admin')).toBeInTheDocument()
    await waitFor(() => expect(confirming).toBe(true))
    release()
    expect(await screen.findByText('outcome: changed')).toBeInTheDocument()
  })

  it('keeps the current token when an older API answers 204 with no body', async () => {
    const { user, fakeApi } = renderApp(<ChangePassword />, {
      routes: { 'POST /v1/auth/password': () => noContent() },
    })
    await screen.findByText('user: Ana Admin')

    await user.click(screen.getByRole('button', { name: 'change' }))

    expect(await screen.findByText('outcome: changed')).toBeInTheDocument()
    expect(session.getToken()).toBe(TEST_TOKEN)
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe(TEST_TOKEN)
    await waitFor(() => expect(fakeApi.calls('GET /v1/auth/me').length).toBeGreaterThan(1))
    for (const request of fakeApi.calls('GET /v1/auth/me')) {
      expect(request.headers.get('Authorization')).toBe(`Bearer ${TEST_TOKEN}`)
    }
  })

  it('lifts the forced-change gate on a 204 too', async () => {
    const me = makeMe({ must_change_password: true })
    let changed = false
    const { user } = renderApp(<ChangePassword />, {
      routes: {
        'POST /v1/auth/password': () => {
          changed = true
          return noContent()
        },
        'GET /v1/auth/me': () => json({ ...me, must_change_password: !changed }),
      },
    })
    await screen.findByText('gate: closed')

    await user.click(screen.getByRole('button', { name: 'change' }))

    expect(await screen.findByText('gate: open')).toBeInTheDocument()
  })

  it.each([
    ['204 with no body', () => noContent()],
    ['200 with a new token', () => json(tokenResponse(NEW_TOKEN))],
  ])('lifts a gate the API raised mid-session (403 PASSWORD_CHANGE_REQUIRED) on a %s, without waiting for the profile', async (_what, answer) => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    let profileCalls = 0
    const { user } = renderApp(<ChangePassword />, {
      routes: {
        'POST /v1/auth/password': () => answer(),
        'GET /v1/auth/me': async () => {
          profileCalls += 1
          if (profileCalls > 1) await held // the confirmation after the change is slow
          return json(makeMe())
        },
      },
    })
    await screen.findByText('user: Ana Admin')
    session.requirePasswordChange(TEST_TOKEN) // as the client does when any call answers 403 PASSWORD_CHANGE_REQUIRED
    expect(await screen.findByText('gate: closed')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'change' }))

    // Open at once: before the confirming GET /auth/me has answered.
    expect(await screen.findByText('gate: open')).toBeInTheDocument()
    expect(session.getSnapshot().passwordChangeRequired).toBe(false)
    release()
    expect(await screen.findByText('outcome: changed')).toBeInTheDocument()
  })

  it.each([
    ['an empty object', {}],
    ['a blank token', { access_token: '' }],
    ['null', null],
  ])('keeps the current token when a 200 answer carries %s', async (_what, body) => {
    const { user } = renderApp(<ChangePassword />, {
      routes: { 'POST /v1/auth/password': () => json(body) },
    })
    await screen.findByText('user: Ana Admin')

    await user.click(screen.getByRole('button', { name: 'change' }))

    expect(await screen.findByText('outcome: changed')).toBeInTheDocument()
    expect(session.getToken()).toBe(TEST_TOKEN)
  })

  it('leaves the token alone when the change is refused', async () => {
    const { user } = renderApp(<ChangePassword />, {
      routes: { 'POST /v1/auth/password': () => problem(422, 'VALIDATION_FAILED') },
    })
    await screen.findByText('user: Ana Admin')

    await user.click(screen.getByRole('button', { name: 'change' }))

    expect(await screen.findByText('outcome: failed ApiError')).toBeInTheDocument()
    expect(session.getToken()).toBe(TEST_TOKEN)
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe(TEST_TOKEN)
  })

  it('does not bring a session back that ended while the change was in flight', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const { user, fakeApi } = renderApp(<ChangePassword />, {
      routes: {
        'POST /v1/auth/password': async () => {
          await held
          return json(tokenResponse(NEW_TOKEN))
        },
      },
    })
    await screen.findByText('user: Ana Admin')

    await user.click(screen.getByRole('button', { name: 'change' }))
    await waitFor(() => expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(1))
    session.end() // the user signed out while the API was still thinking
    release()

    expect(await screen.findByText('outcome: changed')).toBeInTheDocument()
    expect(session.getToken()).toBeNull() // the replacement was dropped, not adopted
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(session.getSnapshot().endedBy).toBe('signedOut')
    expect(fakeApi.calls('GET /v1/auth/me')).toHaveLength(1) // nothing asked for a profile with it
  })

  it('is not undone by a late 401 for the retired token', async () => {
    let lateAnswer!: (response: Response) => void
    const late = new Promise<Response>((resolve) => {
      lateAnswer = resolve
    })
    const { user } = renderApp(<ChangePassword />, {
      routes: {
        ...rotatingApi(makeMe(), { answer: () => json(tokenResponse(NEW_TOKEN)) }),
        'GET /v1/tenants/me': () => late, // asked with the old token, answered after the change
      },
    })
    await screen.findByText('user: Ana Admin')
    const { api } = await import('@/api/client')
    const stray = api.GET('/tenants/me').catch((error: unknown) => error)

    await user.click(screen.getByRole('button', { name: 'change' }))
    await screen.findByText('outcome: changed')
    lateAnswer(problem(401, 'UNAUTHENTICATED'))
    await stray

    expect(session.getToken()).toBe(NEW_TOKEN)
    expect(session.getSnapshot().endedBy).toBeNull()
  })
})
