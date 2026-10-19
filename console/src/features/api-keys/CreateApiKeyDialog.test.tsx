// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import esMX from '@/i18n/es-MX.json'
import {
  fakeApi,
  networkFailure,
  pending,
  refuse,
  resetFakeApi,
  respond,
  SANDBOX_TENANT,
  sentKeys,
} from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { CreateApiKeyDialog } from './CreateApiKeyDialog'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const CREATED = {
  id: 'key-9',
  name: 'ci-bot',
  key_prefix: 'xk_sbx_a4313e2f',
  scopes: ['tenants:read', 'reports:write'],
  env: 'SANDBOX',
  key: 'xk_sbx_a4313e2f_0123456789abcdef0123456789abcdef',
}

const text = esMX.apiKeys
const nameInput = () => screen.getByLabelText(text.create.name)
const customScopeInput = () => screen.getByLabelText(text.scopes.custom)
const addButton = () => screen.getByRole('button', { name: text.scopes.add })
const submitButton = () => screen.getByRole('button', { name: text.create.submit })
const preset = (scope: string) => screen.getByRole('checkbox', { name: new RegExp(scope) })

function setup() {
  const onClose = vi.fn()
  const onCreated = vi.fn()
  const user = userEvent.setup()
  renderWithProviders(<CreateApiKeyDialog onClose={onClose} onCreated={onCreated} />)
  return { user, onClose, onCreated }
}

/** Wait until the tenant (and so the key's environment) has loaded: the form can then be sent. */
async function tenantLoaded() {
  await screen.findByText(esMX.env.SANDBOX)
}

beforeEach(() => {
  resetFakeApi()
  fakeApi.GET.mockImplementation(() => respond(SANDBOX_TENANT))
})

describe('CreateApiKeyDialog: the form', () => {
  it('shows the workspace environment as a fixed value, not a field', async () => {
    setup()

    await tenantLoaded()

    const group = screen.getByRole('group', { name: text.create.env })
    expect(within(group).getByText(esMX.env.SANDBOX)).toBeTruthy()
    expect(group.querySelector('input, select, textarea, button')).toBeNull()
  })

  it('takes the example in the free-entry box from the catalogue, not from a literal', async () => {
    // Overriding the string proves the box reads the catalogue: a hard-coded example would not change.
    const original = esMX.apiKeys.scopes.placeholder
    i18n.addResource('es-MX', 'translation', 'apiKeys.scopes.placeholder', 'informes:read')
    try {
      setup()
      await tenantLoaded()

      expect(customScopeInput().getAttribute('placeholder')).toBe('informes:read')
    } finally {
      i18n.addResource('es-MX', 'translation', 'apiKeys.scopes.placeholder', original)
    }
  })

  it('cannot be sent until the environment is known', async () => {
    fakeApi.GET.mockImplementation(() => pending())
    setup()

    expect(submitButton()).toHaveProperty('disabled', true)
  })

  it('asks for a name and at least one scope, and sends nothing while they are missing', async () => {
    const { user } = setup()
    await tenantLoaded()

    await user.click(submitButton())

    expect(await screen.findByText(esMX.validation.required)).toBeTruthy()
    expect(screen.getByText(esMX.validation.scopesRequired)).toBeTruthy()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('refuses a name longer than the API accepts', async () => {
    const { user } = setup()
    await tenantLoaded()

    await user.click(nameInput())
    await user.paste('x'.repeat(121))
    await user.click(preset('tenants:read'))
    await user.click(submitButton())

    expect(await screen.findByText(esMX.validation.nameTooLong)).toBeTruthy()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('offers the tenants presets only, plus one free entry that must fit the scope grammar', async () => {
    const { user } = setup()
    await tenantLoaded()

    // Only scopes some API route honours are offered; audit:read is not (it can still be typed in).
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
    expect(preset('tenants:read')).toBeTruthy()
    expect(preset('tenants:write')).toBeTruthy()
    expect(screen.queryByRole('checkbox', { name: /audit:read/ })).toBeNull()

    await user.type(customScopeInput(), 'Bad Scope')
    await user.click(addButton())
    expect(await screen.findByText(esMX.validation.scopeFormat)).toBeTruthy()
    expect(screen.queryByRole('list', { name: text.scopes.added })).toBeNull()

    await user.clear(customScopeInput())
    await user.type(customScopeInput(), 'reports:read')
    expect(screen.queryByText(esMX.validation.scopeFormat)).toBeNull() // typing clears the complaint
    await user.click(addButton())

    const chips = screen.getByRole('list', { name: text.scopes.added })
    expect(within(chips).getByText('reports:read')).toBeTruthy()
    expect((customScopeInput() as HTMLInputElement).value).toBe('')
  })

  it('still accepts a scope that is not a preset, such as audit:read, from the free entry', async () => {
    const { user } = setup()
    await tenantLoaded()

    await user.type(customScopeInput(), 'audit:read{Enter}')

    expect(within(screen.getByRole('list', { name: text.scopes.added })).getByText('audit:read')).toBeTruthy()
  })

  it('adds a typed scope with Enter, without sending the form, once per scope, and removes it from its chip', async () => {
    const { user } = setup()
    await tenantLoaded()

    await user.type(customScopeInput(), 'reports:read{Enter}')
    await user.type(customScopeInput(), 'reports:read{Enter}') // the same scope again
    expect(within(screen.getByRole('list', { name: text.scopes.added })).getAllByRole('listitem')).toHaveLength(1)
    expect(fakeApi.POST).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: text.scopes.remove.replace('{{scope}}', 'reports:read') }))
    expect(screen.queryByRole('list', { name: text.scopes.added })).toBeNull()
  })

  it('does not let a scope that was typed but never added slip away silently', async () => {
    const { user } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))
    await user.type(customScopeInput(), 'reports:read')

    await user.click(submitButton())

    expect(await screen.findByText(esMX.validation.scopeDraftPending)).toBeTruthy()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('closes from Cancel', async () => {
    const { user, onClose } = setup()

    await user.click(screen.getByRole('button', { name: esMX.common.cancel }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('CreateApiKeyDialog: submitting', () => {
  it('sends the trimmed name, the chosen and typed scopes and the workspace environment, under a fresh key', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated, onClose } = setup()
    await tenantLoaded()

    await user.type(nameInput(), '  ci-bot  ')
    await user.click(preset('tenants:read'))
    await user.type(customScopeInput(), 'reports:write{Enter}')
    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(fakeApi.POST).toHaveBeenCalledTimes(1)
    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe('/api-keys')
    expect(init?.body).toEqual({ name: 'ci-bot', scopes: ['tenants:read', 'reports:write'], env: 'SANDBOX' })
    expect(init?.headers?.['Idempotency-Key']).toMatch(UUID)
    expect(onCreated).toHaveBeenCalledWith(CREATED) // the caller gets the one-time key
    expect(onClose).not.toHaveBeenCalled() // closing is the caller's decision
  })

  it('treats an answer the API replayed as the success it is', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201, replayed: true }))
    const { user, onCreated } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('sends a repeated submit while the request is out only once', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))
    const form = nameInput().closest('form')
    if (!form) throw new Error('no form')

    fireEvent.submit(form)
    fireEvent.submit(form) // e.g. Enter twice, before the button had time to disable
    fireEvent.submit(form)

    await waitFor(() => expect(fakeApi.POST).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(fakeApi.POST).toHaveBeenCalledTimes(1)
  })

  it('retries a submission whose outcome is unknown with the same Idempotency-Key', async () => {
    fakeApi.POST.mockImplementationOnce(() => networkFailure()).mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())
    expect(await screen.findByText(esMX.errors.NETWORK_ERROR)).toBeTruthy()
    await user.click(submitButton()) // the user presses submit again

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(sentKeys()).toHaveLength(2)
    expect(sentKeys()[1]).toBe(sentKeys()[0])
  })

  it('uses a new key when the retry has edited values', async () => {
    fakeApi.POST.mockImplementationOnce(() => networkFailure()).mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))
    await user.click(submitButton())
    await screen.findByText(esMX.errors.NETWORK_ERROR)

    await user.type(nameInput(), '-2')
    await user.click(submitButton())

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(sentKeys()[1]).not.toBe(sentKeys()[0])
  })

  it('says why in the dialog when the API answers a conflict, and the next try is a new submission', async () => {
    fakeApi.POST.mockImplementationOnce(() => refuse(409, 'CONFLICT')).mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user, onCreated } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(text.create.conflict) // keyed copy, never the server's English detail
    await user.click(submitButton())
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(sentKeys()[1]).not.toBe(sentKeys()[0])
  })

  it('shows the problems of a 422 next to the fields they are about', async () => {
    fakeApi.POST.mockImplementation(() =>
      refuse(422, 'VALIDATION_FAILED', [
        { loc: ['body', 'name'], msg: 'String should have at least 1 character', type: 'string_too_short' },
        { loc: ['body', 'scopes'], msg: 'List should have at least 1 item', type: 'too_short' },
      ]),
    )
    const { user, onCreated } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())

    expect(await screen.findAllByText(esMX.validation.required)).toHaveLength(2)
    expect(nameInput().getAttribute('aria-invalid')).toBe('true')
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('shows a 422 that points at no field as a message about the whole form', async () => {
    fakeApi.POST.mockImplementation(() => refuse(422, 'VALIDATION_FAILED'))
    const { user } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.VALIDATION_FAILED)
  })

  it('cannot be dismissed while the request is out, so the one-time key is never lost', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user, onClose } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())
    await waitFor(() => expect(fakeApi.POST).toHaveBeenCalled())

    await user.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: esMX.common.cancel })).toHaveProperty('disabled', true)
    expect(screen.queryByRole('button', { name: esMX.common.close })).toBeNull() // no corner X either
    expect(screen.getByRole('button', { name: text.create.submitting })).toHaveProperty('disabled', true)
  })

  it('shows a 403 in the user language inside the dialog, keeps the form as it was, and sends the request once', async () => {
    fakeApi.POST.mockImplementation(() => refuse(403, 'ROLE_FORBIDDEN'))
    const { user, onCreated, onClose } = setup()
    await tenantLoaded()
    await user.type(nameInput(), 'ci-bot')
    await user.click(preset('tenants:read'))

    await user.click(submitButton())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN) // keyed copy
    expect(alert.textContent).not.toContain(text.create.conflict) // a refusal is not the environment conflict
    expect(alert.textContent).toContain('req-test') // the reference to quote
    expect(alert.textContent).not.toContain('English detail') // never the server's text
    expect(fakeApi.POST).toHaveBeenCalledTimes(1) // not retried
    expect(onCreated).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect((nameInput() as HTMLInputElement).value).toBe('ci-bot') // the form is as the user left it
    expect(submitButton()).toHaveProperty('disabled', false) // and can be sent again
  })
})
