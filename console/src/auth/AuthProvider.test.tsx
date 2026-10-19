// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fakeApi, refuse, resetFakeApi, respond, type FakeResult } from '@/test/fake-api'
import { makeQueryClient, makeUser } from '@/test/render'
import { AuthProvider } from './AuthProvider'
import { session } from './session'
import { useAuth } from './use-auth'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

/** What `POST /auth/password` answers: the API ends the earlier sessions and issues this one. */
const NEW_SESSION = { access_token: 'new-token', token_type: 'Bearer', expires_in: 28800, must_change_password: false }

interface Call {
  method: 'GET' | 'POST'
  path: string
  /** The token the call carried: what the real client sends as `Authorization: Bearer <token>`. */
  bearer: string | null
}

/** The token the API still honours, and every call it received. */
let honoured: string
let calls: Call[]

/**
 * Answers as the API does where the bearer is concerned. The real client sends the session's token
 * as the bearer, so the fake reads the session at the moment of each call. The API only honours
 * the token it has not ended; any other gets a 401, which the client's middleware answers by
 * expiring that token's session (here too, so a call with a retired token has its real effect).
 */
function asTheApi(method: Call['method'], answer: () => Promise<FakeResult>) {
  return (path: string): Promise<FakeResult> => {
    const bearer = session.getToken()
    calls.push({ method, path, bearer })
    if (bearer !== honoured) {
      session.expire(bearer ?? undefined)
      return refuse(401, 'UNAUTHENTICATED')
    }
    return answer()
  }
}

/** The calls made after the password change went out. */
const afterTheChange = () => calls.slice(calls.findIndex((call) => call.method === 'POST') + 1)

function Probe() {
  const auth = useAuth()
  return (
    <>
      <p data-testid="status">{auth.status}</p>
      <p data-testid="must-change">{String(auth.mustChangePassword)}</p>
      <button
        type="button"
        onClick={() =>
          void auth.changePassword({ currentPassword: 'temporary', newPassword: 'a-new-password-1' }).catch(() => undefined)
        }
      >
        change
      </button>
    </>
  )
}

function setup() {
  const queryClient = makeQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { queryClient, user: userEvent.setup() }
}

const text = (id: string) => screen.getByTestId(id).textContent

beforeEach(() => {
  resetFakeApi()
  calls = []
  honoured = 'old-token'
  session.start('old-token') // signed in with the temporary password
  // The profile says the password is still temporary for as long as the API honours the old token.
  fakeApi.GET.mockImplementation(
    asTheApi('GET', () => respond(makeUser({ must_change_password: honoured === 'old-token' }))),
  )
})

describe('AuthProvider.changePassword', () => {
  it('continues with the token the API returns, because the change ended the session that made it', async () => {
    fakeApi.POST.mockImplementation(
      asTheApi('POST', () => {
        honoured = 'new-token' // the change ends every earlier session and issues this one
        return respond(NEW_SESSION)
      }),
    )
    const { queryClient, user } = setup()
    await waitFor(() => expect(text('must-change')).toBe('true')) // booted, with the gate up

    await user.click(screen.getByRole('button', { name: 'change' }))

    await waitFor(() => expect(text('must-change')).toBe('false'))
    expect(session.getToken()).toBe('new-token') // the old one is no longer good
    expect(session.getSnapshot()).toMatchObject({ endedBy: null, passwordChangeRequired: false })
    expect(text('status')).toBe('authenticated') // no sign-out, no loading screen in between
    expect(queryClient.getQueryData(['me', 'old-token'])).toBeUndefined()
    expect(queryClient.getQueryData(['me', 'new-token'])).toMatchObject({ must_change_password: false })
    // It confirmed with the server, and nothing after the change carried the retired token.
    await waitFor(() => expect(afterTheChange().length).toBeGreaterThan(0))
    expect(afterTheChange().map((call) => call.bearer)).toEqual(afterTheChange().map(() => 'new-token'))
  })

  it('stays on the old session, still gated, when the API refuses the change', async () => {
    fakeApi.POST.mockImplementation(asTheApi('POST', () => refuse(422, 'VALIDATION_FAILED')))
    const { user } = setup()
    await waitFor(() => expect(text('must-change')).toBe('true'))

    await user.click(screen.getByRole('button', { name: 'change' }))

    await waitFor(() => expect(fakeApi.POST).toHaveBeenCalled())
    expect(session.getToken()).toBe('old-token')
    expect(text('must-change')).toBe('true')
    expect(text('status')).toBe('authenticated')
    expect(calls.every((call) => call.bearer === 'old-token')).toBe(true) // nothing ever carried a token that was not issued
  })
})
