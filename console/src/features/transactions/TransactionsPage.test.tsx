// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { PARTY_ID, transactionStaffView } from './fixtures'
import { TransactionsPage } from './TransactionsPage'

const text = esMX.transactions
const LIST = 'GET /v1/transactions'

function setup(routes: FakeRoutes = {}, { path = '/transactions', roles = ['ANALYST'] }: { path?: string; roles?: Role[] } = {}) {
  return renderApp(<TransactionsPage />, {
    path,
    me: makeMe({ roles }),
    routes: {
      [LIST]: () =>
        json({
          items: [
            transactionStaffView(),
            transactionStaffView({ id: 't2', external_id: 'TX-0002', status: 'PARKED', late: true, currency: 'USD', amount_minor: 9_999 }),
          ],
          next_cursor: null,
        }),
      ...routes,
    },
  })
}

async function loadedTable() {
  const name = text.table.label
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

const cells = (row: HTMLElement | undefined) => within(row as HTMLElement)

beforeEach(() => {
  stubPointerCapture()
})

describe('TransactionsPage', () => {
  it('shows the loading table, then the rows with their badges and links', async () => {
    const { fakeApi } = setup()

    expect(screen.getByRole('table', { name: text.table.label }).getAttribute('aria-busy')).toBe('true')
    const table = await loadedTable()

    const [query] = fakeApi.queries(LIST)
    expect(query?.get('limit')).toBe('20')
    expect([...(query?.keys() ?? [])].sort()).toEqual(['limit'])
    const [, first, second] = within(table).getAllByRole('row') as HTMLElement[]
    expect(cells(first).getByRole('link', { name: 'TX-0001' }).getAttribute('href')).toBe('/transactions/t1')
    expect(cells(first).getByText(text.status.PROCESSED)).toBeTruthy()
    expect(cells(first).queryByText(text.lateBadge)).toBeNull()
    expect(cells(first).getByText(`${text.instrument.TRANSFER} · ${text.rail.SPEI}`)).toBeTruthy()
    expect(cells(first).getByText('12,500.00 MXN')).toBeTruthy()
    expect(cells(first).getByRole('link', { name: PARTY_ID.slice(0, 8) }).getAttribute('href')).toBe(`/parties/${PARTY_ID}`)
    expect(cells(second).getByText(text.status.PARKED)).toBeTruthy()
    expect(cells(second).getByText(text.lateBadge)).toBeTruthy()
    expect(cells(second).getByText('99.99 USD')).toBeTruthy()
  })

  it('sends the filters as query parameters and starts again from the first page', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()

    await user.type(screen.getByLabelText(text.filters.partyId), PARTY_ID)
    await user.type(screen.getByLabelText(text.filters.externalId), 'TX-0001')
    await user.type(screen.getByLabelText(text.filters.from), '2026-10-01')
    await user.type(screen.getByLabelText(text.filters.to), '2026-10-31')
    await chooseOption(user, screen.getByLabelText(text.filters.status), text.status.PARKED)
    await chooseOption(user, screen.getByLabelText(text.filters.instrument), text.instrument.TRANSFER)
    await chooseOption(user, screen.getByLabelText(text.filters.rail), text.rail.SPEI)
    await user.click(screen.getByRole('switch', { name: text.filters.late }))
    await user.click(screen.getByRole('button', { name: text.filters.apply }))

    await waitFor(() => expect(fakeApi.queries(LIST).at(-1)?.get('status')).toBe('PARKED'))
    const last = fakeApi.queries(LIST).at(-1)
    expect(Object.fromEntries(last ?? [])).toEqual({
      party_id: PARTY_ID,
      external_id: 'TX-0001',
      from: '2026-10-01',
      to: '2026-10-31',
      status: 'PARKED',
      instrument: 'TRANSFER',
      rail: 'SPEI',
      late: 'true',
      limit: '20',
    })

    await user.click(screen.getByRole('button', { name: text.filters.clear }))
    await waitFor(() => expect([...(fakeApi.queries(LIST).at(-1)?.keys() ?? [])]).toEqual(['limit']))
  })

  it('reads its filters from the URL, as a link from a party sends them', async () => {
    const { fakeApi } = setup({}, { path: `/transactions?party_id=${PARTY_ID}&late=true` })
    await loadedTable()

    const [query] = fakeApi.queries(LIST)
    expect(query?.get('party_id')).toBe(PARTY_ID)
    expect(query?.get('late')).toBe('true')
    expect((screen.getByLabelText(text.filters.partyId) as HTMLInputElement).value).toBe(PARTY_ID)
  })

  it('refuses a reversed date range without asking the API', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()
    const before = fakeApi.calls(LIST).length

    await user.type(screen.getByLabelText(text.filters.from), '2026-10-31')
    await user.type(screen.getByLabelText(text.filters.to), '2026-10-01')
    await user.click(screen.getByRole('button', { name: text.filters.apply }))

    expect(await screen.findByText(text.filters.rangeInvalid)).toBeTruthy()
    expect(fakeApi.calls(LIST)).toHaveLength(before)
  })

  it('refuses a party id that is not a UUID without asking the API', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()
    const before = fakeApi.calls(LIST).length

    await user.type(screen.getByLabelText(text.filters.partyId), 'CLIENTE-1')
    await user.click(screen.getByRole('button', { name: text.filters.apply }))

    expect(await screen.findByText(text.filters.partyIdInvalid)).toBeTruthy()
    expect(fakeApi.calls(LIST)).toHaveLength(before)
  })

  it('moves through pages with the cursor', async () => {
    const { user, fakeApi } = setup({
      [LIST]: (request) =>
        new URL(request.url).searchParams.get('cursor') === 'c2'
          ? json({ items: [transactionStaffView({ id: 't3', external_id: 'TX-0003' })], next_cursor: null })
          : json({ items: [transactionStaffView()], next_cursor: 'c2' }),
    })
    await loadedTable()

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByRole('link', { name: 'TX-0003' })).toBeTruthy()
    expect(fakeApi.queries(LIST).map((query) => query.get('cursor'))).toEqual([null, 'c2'])
  })

  it('says so when there are no transactions', async () => {
    setup({ [LIST]: () => json({ items: [], next_cursor: null }) })

    expect(await screen.findByText(text.empty)).toBeTruthy()
  })

  it('opens for an auditor', async () => {
    setup({}, { roles: ['AUDITOR_RO'] })

    const table = await loadedTable()

    expect(within(table).getByRole('link', { name: 'TX-0001' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: text.title })).toBeTruthy()
  })
})
