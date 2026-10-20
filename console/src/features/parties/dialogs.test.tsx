// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, pending, refuse, resetFakeApi, respond, sentKeys } from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { AddAddressDialog } from './AddAddressDialog'
import { AddContactDialog } from './AddContactDialog'
import { AddIdentifierDialog } from './AddIdentifierDialog'
import { AddRelationshipDialog } from './AddRelationshipDialog'
import { OTHER_PARTY, RFC } from './fixtures'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.parties
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

interface Case {
  name: string
  render: (onClose: () => void) => ReactElement
  title: string
  path: string
  fill: (user: UserEvent) => Promise<void>
  body: unknown
}

const CASES: Case[] = [
  {
    name: 'AddIdentifierDialog',
    render: (onClose) => <AddIdentifierDialog partyId="p1" onClose={onClose} />,
    title: text.identifiers.add,
    path: '/parties/{party_id}/identifiers',
    fill: async (user) => {
      await user.type(screen.getByLabelText(text.identifiers.value), RFC)
    },
    body: { id_type: 'RFC', value: RFC, country_code: 'MX' },
  },
  {
    name: 'AddContactDialog',
    render: (onClose) => <AddContactDialog partyId="p1" onClose={onClose} />,
    title: text.contacts.add,
    path: '/parties/{party_id}/contacts',
    fill: async (user) => {
      await chooseOption(user, screen.getByLabelText(text.contacts.kindLabel), text.contacts.kind.PHONE)
      await user.type(screen.getByLabelText(text.contacts.value), '+525512345678')
      await user.click(screen.getByLabelText(text.contacts.primary))
    },
    body: { kind: 'PHONE', value: '+525512345678', is_primary: true },
  },
  {
    name: 'AddAddressDialog',
    render: (onClose) => <AddAddressDialog partyId="p1" onClose={onClose} />,
    title: text.addresses.add,
    path: '/parties/{party_id}/addresses',
    fill: async (user) => {
      await chooseOption(user, screen.getByLabelText(text.addresses.typeLabel), text.addresses.type.FISCAL)
      await user.type(screen.getByLabelText(text.addresses.street), 'Av. Reforma')
      await user.type(screen.getByLabelText(text.addresses.postalCode), '06600')
    },
    body: { address_type: 'FISCAL', lines: { street: 'Av. Reforma' }, country_code: 'MX', postal_code: '06600' },
  },
  {
    name: 'AddRelationshipDialog',
    render: (onClose) => <AddRelationshipDialog partyId="p1" onClose={onClose} />,
    title: text.relationships.add,
    path: '/parties/{party_id}/relationships',
    fill: async (user) => {
      await user.type(screen.getByLabelText(text.relationships.to), OTHER_PARTY)
      await user.type(screen.getByLabelText(text.relationships.ownership), '25.5')
    },
    body: { to_party_id: OTHER_PARTY, rel_type: 'SHAREHOLDER', ownership_pct: '25.5' },
  },
]

function setup(item: Case) {
  const onClose = vi.fn()
  const user = userEvent.setup()
  renderWithProviders(item.render(onClose))
  return { user, onClose }
}

const save = () => screen.getByRole('button', { name: text.save })

beforeEach(() => {
  resetFakeApi()
  stubPointerCapture()
})

describe.each(CASES)('$name', (item) => {
  it('posts its body with an Idempotency-Key, then closes', async () => {
    fakeApi.POST.mockImplementation(() => respond({}, { status: 201 }))
    const { user, onClose } = setup(item)
    expect(screen.getByRole('dialog', { name: item.title })).toBeTruthy()
    await item.fill(user)

    await user.click(save())

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe(item.path)
    expect(init?.params?.path).toEqual({ party_id: 'p1' })
    expect(init?.body).toEqual(item.body)
    expect(sentKeys()[0]).toMatch(UUID)
  })

  it('cannot be dismissed while the request is out', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user, onClose } = setup(item)
    await item.fill(user)

    await user.click(save())

    expect(await screen.findByRole('button', { name: text.saving })).toBeDisabled()
    expect(screen.getByRole('button', { name: esMX.common.cancel })).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: item.title })).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('shows a 422 without field errors as a whole, and stays open', async () => {
    fakeApi.POST.mockImplementation(() => refuse(422, 'VALIDATION_FAILED'))
    const { user, onClose } = setup(item)
    await item.fill(user)

    await user.click(save())

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.VALIDATION_FAILED)
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('field errors from the server', () => {
  it('reach their input, nested ones included', async () => {
    fakeApi.POST.mockImplementation(() =>
      refuse(422, 'VALIDATION_FAILED', [{ loc: ['body', 'lines', 'street'], msg: 'too long', type: 'string_too_long' }]),
    )
    const item = CASES[2]
    if (item === undefined) throw new Error('no address case')
    const { user } = setup(item)
    await item.fill(user)

    await user.click(save())

    const dialog = screen.getByRole('dialog', { name: item.title })
    expect((await within(dialog).findByRole('alert')).textContent).toBe(esMX.validation.tooLong)
    expect(screen.getByLabelText(text.addresses.street)).toHaveAttribute('aria-invalid', 'true')
  })
})

describe('AddIdentifierDialog refusals', () => {
  const item = CASES[0]

  it.each([
    [409, 'IDENTIFIER_EXISTS', text.create.identifierExists],
    [422, 'IDENTIFIER_INVALID', text.create.identifierInvalid],
  ] as const)('shows %s %s with its own message', async (status, code, message) => {
    fakeApi.POST.mockImplementation(() => refuse(status, code))
    if (item === undefined) throw new Error('no identifier case')
    const { user } = setup(item)
    await item.fill(user)

    await user.click(save())

    expect((await screen.findByRole('alert')).textContent).toContain(message)
  })

  it('checks the value before sending anything', async () => {
    if (item === undefined) throw new Error('no identifier case')
    const { user } = setup(item)

    await user.click(save())

    expect((await screen.findByRole('alert')).textContent).toBe(esMX.validation.required)
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })
})
