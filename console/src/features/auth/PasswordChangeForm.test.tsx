// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import type { UserEvent } from '@testing-library/user-event'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { session } from '@/auth/session'
import esMX from '@/i18n/es-MX.json'
import { json, noContent, problem } from '@/test/fake-network'
import { renderApp, TEST_TOKEN } from '@/test/render-app'
import { PasswordChangeForm } from './PasswordChangeForm'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const es = esMX.auth.setupPassword
const v = esMX.validation

const OLD = 'the-old-password-1'
const NEW = 'a-brand-new-password'

const current = () => screen.getByLabelText(es.current)
const next = () => screen.getByLabelText(es.new)
const confirm = () => screen.getByLabelText(es.confirm)
const submit = () => screen.getByRole('button', { name: es.submit })

async function fill(user: UserEvent, values: { current?: string; next?: string; confirm?: string } = {}) {
  const newPassword = values.next ?? NEW
  await user.type(current(), values.current ?? OLD)
  await user.type(next(), newPassword)
  await user.type(confirm(), values.confirm ?? newPassword)
}

const tokenResponse = (token: string) => ({
  access_token: token,
  token_type: 'Bearer',
  expires_in: 28800,
  must_change_password: false,
})

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('PasswordChangeForm: checks before it asks the API', () => {
  it('asks for every field', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />)

    await user.click(submit())

    expect(await screen.findAllByText(v.required)).toHaveLength(2) // current and confirmation
    expect(screen.getByText(v.passwordMin)).toBeInTheDocument() // the new password
    expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(0)
  })

  it('wants at least 12 characters in the new password', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />)

    await fill(user, { next: 'elevenchars' }) // 11
    await user.click(submit())

    expect(await screen.findByText(v.passwordMin)).toBeInTheDocument()
    expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(0)
  })

  it('accepts exactly 12 characters', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />, {
      routes: { 'POST /v1/auth/password': () => noContent() },
    })

    await fill(user, { next: 'twelve-chars' }) // 12
    await user.click(submit())

    await waitFor(() => expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(1))
    expect(screen.queryByText(v.passwordMin)).toBeNull()
  })

  it('wants the confirmation to match', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />)

    await fill(user, { confirm: 'a-different-password' })
    await user.click(submit())

    expect(await screen.findByText(v.passwordMismatch)).toBeInTheDocument()
    expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(0)
  })

  it('wants a new password that differs from the current one', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />)

    await fill(user, { current: NEW })
    await user.click(submit())

    expect(await screen.findByText(v.passwordSame)).toBeInTheDocument()
    expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(0)
  })

  it('marks the fields it rejects and describes them to assistive technology', async () => {
    const { user } = renderApp(<PasswordChangeForm />)

    await user.click(submit())

    await screen.findByText(v.passwordMin)
    expect(next()).toHaveAttribute('aria-invalid', 'true')
    expect(next().getAttribute('aria-describedby')).toContain('-error')
  })
})

describe('PasswordChangeForm: changing the password', () => {
  it('sends the current and the new password, and nothing else, to /auth/password', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />, {
      routes: { 'POST /v1/auth/password': () => noContent() },
    })

    await fill(user)
    await user.click(submit())

    await waitFor(() => expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(1))
    const [request] = fakeApi.calls('POST /v1/auth/password')
    expect(await request?.json()).toEqual({ current_password: OLD, new_password: NEW })
    expect(request?.headers.get('Authorization')).toBe(`Bearer ${TEST_TOKEN}`)
    expect(request?.headers.has('Idempotency-Key')).toBe(false) // /auth routes take none
  })

  it('on success clears the fields, says so, and tells its caller', async () => {
    const onChanged = vi.fn()
    const { user } = renderApp(<PasswordChangeForm onChanged={onChanged} />, {
      routes: { 'POST /v1/auth/password': () => json(tokenResponse('rotated-token')) },
    })

    await fill(user)
    await user.click(submit())

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(es.success))
    expect(onChanged).toHaveBeenCalledTimes(1)
    for (const field of [current(), next(), confirm()]) expect(field).toHaveValue('') // not left on screen
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('keeps working with the token the API replaces the old one with', async () => {
    const { user, fakeApi } = renderApp(<PasswordChangeForm />, {
      routes: { 'POST /v1/auth/password': () => json(tokenResponse('rotated-token')) },
    })

    await fill(user)
    await user.click(submit())

    await waitFor(() => expect(session.getToken()).toBe('rotated-token'))
    await waitFor(() => expect(fakeApi.calls('GET /v1/auth/me').length).toBeGreaterThan(1))
    expect(fakeApi.calls('GET /v1/auth/me').at(-1)?.headers.get('Authorization')).toBe('Bearer rotated-token')
  })

  it('shows a refusal (422) inside the form, keeps what was typed, and clears it on the next success', async () => {
    let accept = false
    const onChanged = vi.fn()
    const { user } = renderApp(<PasswordChangeForm onChanged={onChanged} />, {
      routes: {
        'POST /v1/auth/password': () => (accept ? noContent() : problem(422, 'VALIDATION_FAILED')),
      },
    })

    await fill(user)
    await user.click(submit())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(es.rejected)
    expect(alert).not.toHaveTextContent('English detail') // never the server's words
    expect(current()).toHaveValue(OLD)
    expect(next()).toHaveValue(NEW)
    expect(toast.success).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
    expect(session.getToken()).toBe(TEST_TOKEN)

    accept = true
    await user.click(submit())

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(es.rejected)).toBeNull()
  })

  it('reports a server failure in the user\'s language, with the request id', async () => {
    const { user } = renderApp(<PasswordChangeForm />, {
      routes: { 'POST /v1/auth/password': () => problem(500, 'INTERNAL') },
    })

    await fill(user)
    await user.click(submit())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(esMX.errors.INTERNAL)
    expect(alert).toHaveTextContent('req-test')
  })

  it('reports an unreachable server', async () => {
    const { user } = renderApp(<PasswordChangeForm />, {
      routes: {
        'POST /v1/auth/password': () => {
          throw new TypeError('fetch failed')
        },
      },
    })

    await fill(user)
    await user.click(submit())

    expect(await screen.findByRole('alert')).toHaveTextContent(esMX.errors.NETWORK_ERROR)
  })

  it('holds the button while the request is out, so a second click sends nothing more', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const { user, fakeApi } = renderApp(<PasswordChangeForm />, {
      routes: {
        'POST /v1/auth/password': async () => {
          await held
          return noContent()
        },
      },
    })
    await fill(user)

    await user.click(submit())
    const busy = await screen.findByRole('button', { name: es.submitting })
    expect(busy).toBeDisabled()
    await user.click(busy)

    expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(1)
    release()
    await waitFor(() => expect(screen.getByRole('button', { name: es.submit })).toBeEnabled())
    expect(fakeApi.calls('POST /v1/auth/password')).toHaveLength(1)
  })
})

describe('PasswordChangeForm: focus', () => {
  it('stays out of the way by default', () => {
    renderApp(<PasswordChangeForm />)

    expect(current()).not.toHaveFocus()
  })

  it('puts the cursor in the first field when it is the whole page', () => {
    renderApp(<PasswordChangeForm autoFocus />)

    expect(current()).toHaveFocus()
  })
})
