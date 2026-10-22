// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Link } from 'react-router-dom'
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
  return renderApp(
    <>
      <Link to="/transactions">sidebar link</Link>
      <TransactionsPage />
    </>,
    {
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
  },
  )
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

  it('starts again from the first page when a link changes the filters', async () => {
    const { user, fakeApi } = setup(
      {
        [LIST]: (request) =>
          new URL(request.url).searchParams.get('cursor') === 'c2'
            ? json({ items: [transactionStaffView({ id: 't3', external_id: 'TX-0003' })], next_cursor: null })
            : json({ items: [transactionStaffView()], next_cursor: 'c2' }),
      },
      { path: `/transactions?party_id=${PARTY_ID}` },
    )
    await loadedTable()
    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))
    await screen.findByRole('link', { name: 'TX-0003' })

    await user.click(screen.getByRole('link', { name: 'sidebar link' }))

    await waitFor(() => expect(fakeApi.queries(LIST).at(-1)?.has('party_id')).toBe(false))
    expect(Object.fromEntries(fakeApi.queries(LIST).at(-1) ?? [])).toEqual({ limit: '20' })
    expect(await screen.findByRole('link', { name: 'TX-0001' })).toBeTruthy()
  })

  it('shows the time of day in Mexico City, the zone the date filters use', async () => {
    setup({
      [LIST]: () => json({ items: [transactionStaffView({ occurred_at: '2026-10-21T02:00:00Z' })], next_cursor: null }),
    })
    const table = await loadedTable()

    const time = within(table).getByText((_, element) => element?.tagName === 'TIME')
    expect(time.getAttribute('dateTime')).toBe('2026-10-21T02:00:00Z')
    expect(time.textContent).toContain('20 oct 2026')
    expect(time.textContent).toContain('8:00')
    expect(time.textContent).not.toContain('2:00')
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

  it('shows the files and batches panels, and the parked and backfill lists behind their tabs', async () => {
    const { user, fakeApi } = setup({
      'GET /v1/transactions/parked': () =>
        json({ items: [transactionStaffView({ id: 'tp', external_id: 'TX-PARKED', status: 'PARKED' })], next_cursor: null }),
      'GET /v1/transactions/backfill': () =>
        json({ items: [transactionStaffView({ id: 'tb', external_id: 'TX-LATE', status: 'BACKFILL' })], next_cursor: null }),
    })
    await loadedTable()

    expect(screen.getByText(text.files.title)).toBeTruthy()
    expect(screen.getByText(text.batches.title)).toBeTruthy()
    expect(fakeApi.calls('GET /v1/transactions/parked')).toEqual([])

    await user.click(screen.getByRole('tab', { name: text.tabs.parked }))
    expect(await screen.findByText('TX-PARKED')).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: text.tabs.backfill }))
    expect(await screen.findByText('TX-LATE')).toBeTruthy()
    expect(screen.queryByRole('button', { name: text.backfill.run })).toBeNull()
  })
})

