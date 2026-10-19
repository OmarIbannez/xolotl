// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, networkFailure, pending, refuse, resetFakeApi, respond, sentKeys } from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { CreateStaffDialog } from './CreateStaffDialog'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const CREATED = {
  id: 'staff-9',
  email: 'ana.perez@acme.mx',
  display_name: 'Ana Pérez',
  roles: ['MLRO'],
  initial_password: 'Initial-pass_0123456789',
}

const text = esMX.staff
const emailInput = () => screen.getByLabelText(text.create.email)
const nameInput = () => screen.getByLabelText(text.create.displayName)
const role = (name: keyof typeof esMX.roles) => screen.getByRole('checkbox', { name: esMX.roles[name] })
const submitButton = () => screen.getByRole('button', { name: text.create.submit })

function setup() {
  const onClose = vi.fn()
  const onCreated = vi.fn()
  const user = userEvent.setup()
  renderWithProviders(<CreateStaffDialog onClose={onClose} onCreated={onCreated} />)
  return { user, onClose, onCreated }
}

/** A valid form: Ana, MLRO. */
async function fillIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(emailInput(), 'ana.perez@acme.mx')
  await user.type(nameInput(), 'Ana Pérez')
  await user.click(role('MLRO'))
}

beforeEach(() => {
  resetFakeApi()
})

describe('CreateStaffDialog: the form', () => {
  it('asks for an email, a name and roles, offering all five roles with none chosen', () => {
    setup()

    expect(emailInput()).toBeTruthy()
    expect(nameInput()).toBeTruthy()
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes.map((box) => box.getAttribute('aria-checked'))).toEqual(['false', 'false', 'false', 'false', 'false'])
    for (const name of ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN'] as const) {
      expect(role(name)).toBeTruthy()
    }
  })

  it('requires every field and sends nothing while one is missing', async () => {
    const { user } = setup()

    await user.click(submitButton())

    expect(await screen.findAllByText(esMX.validation.required)).toHaveLength(2) // email and name
    expect(screen.getByText(esMX.validation.rolesRequired)).toBeTruthy()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('refuses an email that is not one, with the same rule as the sign-in form', async () => {
    const { user } = setup()
    await user.type(emailInput(), 'not-an-email')
    await user.type(nameInput(), 'Ana')
    await user.click(role('MLRO'))

    await user.click(submitButton())

    expect(await screen.findByText(esMX.validation.email)).toBeTruthy()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('refuses a display name longer than the API accepts', async () => {
    const { user } = setup()
    await user.type(emailInput(), 'ana@acme.mx')
    await user.click(nameInput())
    await user.paste('x'.repeat(201))
    await user.click(role('MLRO'))

    await user.click(submitButton())

    expect(await screen.findByText(esMX.validation.displayNameTooLong)).toBeTruthy()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('closes from Cancel', async () => {
    const { user, onClose } = setup()

    await user.click(screen.getByRole('button', { name: esMX.common.cancel }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('CreateStaffDialog: submitting', () => {
  it('sends the trimmed email and name with the chosen roles under a fresh key, and hands over the response', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated, onClose } = setup()

    await user.type(emailInput(), '  ana.perez@acme.mx ')
    await user.type(nameInput(), ' Ana Pérez ')
    await user.click(role('MLRO'))
    await user.click(role('SENIOR_ANALYST'))
    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(fakeApi.POST).toHaveBeenCalledTimes(1)
    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe('/staff')
    expect(init?.body).toEqual({
      email: 'ana.perez@acme.mx',
      display_name: 'Ana Pérez',
      roles: ['MLRO', 'SENIOR_ANALYST'],
    })
    expect(init?.headers?.['Idempotency-Key']).toMatch(UUID)
    expect(onCreated).toHaveBeenCalledWith(CREATED) // the caller gets the one-time password
    expect(onClose).not.toHaveBeenCalled() // closing is the caller's decision
  })

  it('lets a role be unchosen again before sending', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await user.type(emailInput(), 'ana@acme.mx')
    await user.type(nameInput(), 'Ana')
    await user.click(role('ADMIN'))
    await user.click(role('ANALYST'))
    await user.click(role('ADMIN')) // changed their mind

    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(fakeApi.POST.mock.calls[0]?.[1]?.body).toMatchObject({ roles: ['ANALYST'] })
  })

  it('treats an answer the API replayed as the success it is', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201, replayed: true }))
    const { user, onCreated } = setup()
    await fillIn(user)

    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('retries a submission whose outcome is unknown with the same Idempotency-Key', async () => {
    fakeApi.POST.mockImplementationOnce(() => networkFailure()).mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await fillIn(user)

    await user.click(submitButton())
    expect(await screen.findByText(esMX.errors.NETWORK_ERROR)).toBeTruthy()
    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(sentKeys()).toHaveLength(2)
    expect(sentKeys()[1]).toBe(sentKeys()[0])
  })

  it('retries a server fault with the same key too, since the account may have been created', async () => {
    fakeApi.POST.mockImplementationOnce(() => refuse(500, 'INTERNAL')).mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await fillIn(user)

    await user.click(submitButton())
    expect(await screen.findByText(esMX.errors.INTERNAL)).toBeTruthy()
    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(sentKeys()[1]).toBe(sentKeys()[0])
  })

  it('sends a repeated submit while the request is out only once', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user } = setup()
    await fillIn(user)
    const form = emailInput().closest('form')
    if (!form) throw new Error('no form')

    await user.click(submitButton())
    await waitFor(() => expect(fakeApi.POST).toHaveBeenCalled())
    form.requestSubmit() // Enter pressed again while it waits
    form.requestSubmit()
    await new Promise((resolve) => setTimeout(resolve, 30))

    expect(fakeApi.POST).toHaveBeenCalledTimes(1)
  })

  it('says a user with that email exists when the API answers a conflict, and the next try is a new submission', async () => {
    fakeApi.POST.mockImplementationOnce(() => refuse(409, 'CONFLICT')).mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await fillIn(user)

    await user.click(submitButton())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(text.create.conflict) // keyed copy, never the server's English detail
    await user.type(emailInput(), 'x')
    await user.click(submitButton())
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(sentKeys()[1]).not.toBe(sentKeys()[0])
  })

  it('shows the problems of a 422 next to the fields they are about', async () => {
    fakeApi.POST.mockImplementation(() =>
      refuse(422, 'VALIDATION_FAILED', [
        { loc: ['body', 'display_name'], msg: 'String should have at least 1 character', type: 'string_too_short' },
        { loc: ['body', 'roles', 0], msg: "Input should be 'ANALYST', ...", type: 'enum' },
      ]),
    )
    const { user, onCreated } = setup()
    await fillIn(user)

    await user.click(submitButton())

    expect(await screen.findByText(esMX.validation.required)).toBeTruthy() // display_name
    expect(screen.getByText(esMX.validation.invalid)).toBeTruthy() // the role
    expect(nameInput().getAttribute('aria-invalid')).toBe('true')
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('shows a 422 that points at no field (a malformed email) as a message about the whole form', async () => {
    fakeApi.POST.mockImplementation(() => refuse(422, 'VALIDATION_FAILED'))
    const { user } = setup()
    await fillIn(user)

    await user.click(submitButton())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.VALIDATION_FAILED)
  })

  it('cannot be dismissed while the request is out, so the one-time password is never lost', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user, onClose } = setup()
    await fillIn(user)

    await user.click(submitButton())
    await waitFor(() => expect(fakeApi.POST).toHaveBeenCalled())

    await user.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: esMX.common.cancel })).toHaveProperty('disabled', true)
    expect(screen.queryByRole('button', { name: esMX.common.close })).toBeNull()
    expect(screen.getByRole('button', { name: text.create.submitting })).toHaveProperty('disabled', true)
  })

  it('shows a 403 in the user language inside the dialog, keeps the form as it was, and sends the request once', async () => {
    fakeApi.POST.mockImplementation(() => refuse(403, 'ROLE_FORBIDDEN'))
    const { user, onCreated, onClose } = setup()
    await fillIn(user)

    await user.click(submitButton())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN) // keyed copy
    expect(alert.textContent).not.toContain(text.create.conflict) // a refusal is not the duplicate-email conflict
    expect(alert.textContent).toContain('req-test') // the reference to quote
    expect(alert.textContent).not.toContain('English detail') // never the server's text
    expect(fakeApi.POST).toHaveBeenCalledTimes(1) // not retried
    expect(onCreated).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect((emailInput() as HTMLInputElement).value).toBe('ana.perez@acme.mx') // the form is as the user left it
    expect(submitButton()).toHaveProperty('disabled', false) // and can be sent again
  })
})
