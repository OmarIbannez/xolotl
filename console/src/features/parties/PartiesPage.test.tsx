// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PartyResponse, Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, pending, refuse, resetFakeApi, respond } from '@/test/fake-api'
import { makeUser, renderWithProviders } from '@/test/render'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { party } from './fixtures'
import { PartiesPage } from './PartiesPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const text = esMX.parties
const ORG = party({
  id: 'p2',
  party_type: 'ORGANIZATION',
  display_name: 'A.S.',
  identifiers: [{ id: 'i2', id_type: 'CURP', country_code: 'MX', masked_value: '****XY01', valid_from: null, valid_to: null }],
})

type Page = { items: PartyResponse[]; next_cursor: string | null }

function serve(pages: Record<string, Page>) {
  fakeApi.GET.mockImplementation((path, init) => {
    const page = pages[String(init?.params?.query?.cursor ?? 'first')]
    return path === '/parties' && page ? respond(page) : refuse(404, 'NOT_FOUND')
  })
}

const listCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/parties')
const table = () => screen.getByRole('table', { name: text.table.label })

function setup(roles: Role[] = ['ANALYST']) {
  const user = userEvent.setup()
  renderWithProviders(
    <MemoryRouter>
      <PartiesPage />
    </MemoryRouter>,
    { user: makeUser({ roles }) },
  )
  return { user }
}

beforeEach(() => {
  resetFakeApi()
  stubPointerCapture()
  serve({ first: { items: [party(), ORG], next_cursor: null } })
})

describe('PartiesPage: states', () => {
  it('shows the table header over placeholders while the first page loads', () => {
    fakeApi.GET.mockImplementation(() => pending())
    setup()

    expect(table().getAttribute('aria-busy')).toBe('true')
  })

  it('says there are no parties yet', async () => {
    serve({ first: { items: [], next_cursor: null } })
    setup()

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('lists each party with a link, its type, masked identifiers, status and date', async () => {
    setup()

    const link = await screen.findByRole('link', { name: 'A.M.P.G.' })
    expect(link.getAttribute('href')).toBe('/parties/p1')
    const row = within(table()).getByRole('row', { name: /A\.M\.P\.G\./ })
    expect(within(row).getByText(text.type.PERSON)).toBeTruthy()
    expect(row.textContent).toContain('****01P8')
    expect(row.textContent).toContain(text.identifierType.RFC)
    expect(within(row).getByText(text.status.ACTIVE)).toBeTruthy()
    const org = within(table()).getByRole('row', { name: /A\.S\./ })
    expect(within(org).getByText(text.type.ORGANIZATION)).toBeTruthy()
    expect(org.textContent).toContain('****XY01')
  })

  it('names a party without a display name as such', async () => {
    serve({ first: { items: [party({ display_name: null })], next_cursor: null } })
    setup()

    expect(await screen.findByRole('link', { name: text.table.noName })).toBeTruthy()
  })

  it('shows a failed load as a message with a reference, and retries', async () => {
    fakeApi.GET.mockImplementation(() => refuse(500, 'INTERNAL'))
    const { user } = setup()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.INTERNAL)
    expect(alert.textContent).toContain('req-test')

    serve({ first: { items: [party()], next_cursor: null } })
    await user.click(within(alert).getByRole('button', { name: esMX.common.retry }))

    expect(await screen.findByRole('link', { name: 'A.M.P.G.' })).toBeTruthy()
  })
})

describe('PartiesPage: identifier search', () => {
  it('sends exactly one request with id_type and value after submit, and none while typing', async () => {
    const { user } = setup()
    await screen.findByRole('link', { name: 'A.M.P.G.' })
    const before = listCalls().length

    await chooseOption(user, screen.getByRole('combobox', { name: text.search.idType }), text.identifierType.CURP)
    await user.type(screen.getByRole('textbox', { name: text.search.value }), 'PEGA760510MDFRMN09')
    expect(listCalls()).toHaveLength(before)

    serve({ first: { items: [], next_cursor: null } })
    await user.click(screen.getByRole('button', { name: text.search.submit }))

    expect(await screen.findByText(text.emptyFiltered)).toBeTruthy()
    expect(listCalls()).toHaveLength(before + 1)
    expect(listCalls().at(-1)?.[1]?.params?.query).toMatchObject({
      limit: 20,
      id_type: 'CURP',
      value: 'PEGA760510MDFRMN09',
    })

    await user.click(screen.getByRole('button', { name: text.search.clear }))
    await waitFor(() => expect(listCalls().at(-1)?.[1]?.params?.query).not.toHaveProperty('id_type'))
    expect((screen.getByRole('textbox', { name: text.search.value }) as HTMLInputElement).value).toBe('')
  })
})

describe('PartiesPage: paging', () => {
  it('asks for the next page with the cursor the API gave', async () => {
    serve({ first: { items: [party()], next_cursor: 'cursor-2' }, 'cursor-2': { items: [ORG], next_cursor: null } })
    const { user } = setup()
    await screen.findByRole('link', { name: 'A.M.P.G.' })

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByRole('link', { name: 'A.S.' })).toBeTruthy()
    expect(listCalls().map(([, init]) => init?.params?.query?.cursor)).toEqual([undefined, 'cursor-2'])
  })
})

describe('PartiesPage: roles', () => {
  it('offers the new button to a writer', async () => {
    setup(['ANALYST'])
    expect(await screen.findByRole('button', { name: text.new })).toBeTruthy()
  })

  it('hides the new button from AUDITOR_RO', async () => {
    setup(['AUDITOR_RO'])
    await screen.findByRole('link', { name: 'A.M.P.G.' })

    expect(screen.queryByRole('button', { name: text.new })).toBeNull()
  })
})

describe('PartiesPage: what comes back from the API is text, never markup', () => {
  it('shows a display name like markup as that literal text', async () => {
    const MARKUP = '<img src=x onerror="window.__pwned=1">'
    serve({ first: { items: [party({ display_name: MARKUP })], next_cursor: null } })
    setup()

    expect(await screen.findByRole('link', { name: MARKUP })).toBeTruthy()
    expect(document.body.querySelector('img')).toBeNull()
    expect(Reflect.get(window, '__pwned')).toBeUndefined()
  })
})
