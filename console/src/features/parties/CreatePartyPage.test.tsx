// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, networkFailure, pending, refuse, resetFakeApi, respond, sentKeys } from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { CreatePartyPage } from './CreatePartyPage'
import { party, RFC } from './fixtures'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const text = esMX.parties.create
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const input = (label: string) => screen.getByLabelText(label)
const submit = () => screen.getByRole('button', { name: text.submit })

function setup() {
  const user = userEvent.setup()
  renderWithProviders(
    <MemoryRouter initialEntries={['/parties/new']}>
      <Routes>
        <Route path="/parties/new" element={<CreatePartyPage />} />
        <Route path="/parties/:partyId" element={<p>detail page</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return { user }
}

async function fillPerson(user: ReturnType<typeof userEvent.setup>) {
  await user.type(input(text.givenNames), 'Ana María')
  await user.type(input(text.firstSurname), 'Pérez')
  await user.type(input(text.identifierValue), RFC)
}

beforeEach(() => {
  resetFakeApi()
  vi.mocked(toast.success).mockClear()
})

describe('CreatePartyPage', () => {
  it('submits the request shape with an Idempotency-Key, then opens the new party', async () => {
    fakeApi.POST.mockImplementation(() => respond(party({ id: 'p9' }), { status: 201 }))
    const { user } = setup()
    await fillPerson(user)

    await user.click(submit())

    expect(await screen.findByText('detail page')).toBeTruthy()
    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe('/parties')
    expect(init?.body).toEqual({
      party_type: 'PERSON',
      country_code: 'MX',
      person: { given_names: 'Ana María', first_surname: 'Pérez' },
      identifiers: [{ id_type: 'RFC', value: RFC, country_code: 'MX' }],
    })
    expect(sentKeys()[0]).toMatch(UUID)
    expect(toast.success).toHaveBeenCalledWith(text.done)
  })

  it('switches to an organisation, keeping the identifier, and sends its legal name', async () => {
    fakeApi.POST.mockImplementation(() => respond(party({ id: 'p9' }), { status: 201 }))
    const { user } = setup()
    await user.type(input(text.identifierValue), 'AAA010101AAA')

    await user.click(screen.getByRole('button', { name: esMX.parties.type.ORGANIZATION }))
    expect(screen.queryByLabelText(text.givenNames)).toBeNull()
    await user.type(input(text.legalName), 'Acme SA de CV')
    await user.click(submit())

    await screen.findByText('detail page')
    expect(fakeApi.POST.mock.calls[0]?.[1]?.body).toEqual({
      party_type: 'ORGANIZATION',
      country_code: 'MX',
      organization: { legal_name: 'Acme SA de CV' },
      identifiers: [{ id_type: 'RFC', value: 'AAA010101AAA', country_code: 'MX' }],
    })
  })

  it('sends nothing while a required field is missing', async () => {
    const { user } = setup()

    await user.click(submit())

    expect(await screen.findAllByText(esMX.validation.required)).toHaveLength(3)
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('says so when a party with that identifier exists (409 IDENTIFIER_EXISTS)', async () => {
    fakeApi.POST.mockImplementation(() => refuse(409, 'IDENTIFIER_EXISTS'))
    const { user } = setup()
    await fillPerson(user)

    await user.click(submit())

    expect((await screen.findByRole('alert')).textContent).toBe(text.identifierExists)
  })

  it('says so when the identifier fails its validator (422 IDENTIFIER_INVALID)', async () => {
    fakeApi.POST.mockImplementation(() => refuse(422, 'IDENTIFIER_INVALID'))
    const { user } = setup()
    await fillPerson(user)

    await user.click(submit())

    expect((await screen.findByRole('alert')).textContent).toBe(text.identifierInvalid)
  })

  it('points a server-side field error of the person at its input', async () => {
    fakeApi.POST.mockImplementation(() =>
      refuse(422, 'VALIDATION_FAILED', [{ loc: ['body', 'person', 'given_names'], msg: 'too long', type: 'string_too_long' }]),
    )
    const { user } = setup()
    await fillPerson(user)

    await user.click(submit())

    expect(await screen.findByText(esMX.validation.tooLong)).toBeTruthy()
  })

  it('reuses one key when a network failure is retried', async () => {
    fakeApi.POST.mockImplementationOnce(() => networkFailure()).mockImplementation(() =>
      respond(party({ id: 'p9' }), { status: 201 }),
    )
    const { user } = setup()
    await fillPerson(user)

    await user.click(submit())
    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.NETWORK_ERROR)
    await user.click(submit())

    await screen.findByText('detail page')
    expect(sentKeys()).toHaveLength(2)
    expect(sentKeys()[1]).toBe(sentKeys()[0])
  })

  it('disables the submit button while the request is out', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user } = setup()
    await fillPerson(user)

    await user.click(submit())

    await waitFor(() => expect(screen.getByRole('button', { name: text.submitting })).toHaveProperty('disabled', true))
  })
})
