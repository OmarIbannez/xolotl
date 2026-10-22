// @vitest-environment jsdom
import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { parkedTransaction, staffTransaction } from './ingest-fixtures'
import { PartyTransactionsCard } from './PartyTransactionsCard'

const text = esMX.parties.transactions
const LIST = 'GET /v1/transactions'

describe('PartyTransactionsCard', () => {
  it("asks for the party's latest transactions as plain query parameters", async () => {
    const { fakeApi } = renderApp(<PartyTransactionsCard partyId="p1" />, {
      path: '/parties/p1',
      me: makeMe({ roles: ['AUDITOR_RO'] }),
      routes: { [LIST]: () => json({ items: [staffTransaction()], next_cursor: null }) },
    })

    await screen.findByText('ext-0001')
    const [query] = fakeApi.queries(LIST)
    expect(query?.get('party_id')).toBe('p1')
    expect(query?.get('limit')).toBe('20')
    expect([...(query?.keys() ?? [])].sort()).toEqual(['limit', 'party_id'])
  })

  it('renders each row with its amount and status, linking to the transaction and to the whole list', async () => {
    renderApp(<PartyTransactionsCard partyId="p1" />, {
      path: '/parties/p1',
      routes: { [LIST]: () => json({ items: [staffTransaction(), parkedTransaction()], next_cursor: 'more' }) },
    })

    await screen.findByText('ext-0001')
    const table = screen.getByRole('table', { name: text.label })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'ext-0001' }).getAttribute('href')).toBe('/transactions/t1')
    expect(within(rows[0] as HTMLElement).getByText(/12,500\.50/)).toBeTruthy()
    expect(within(rows[0] as HTMLElement).getByText(esMX.transactions.status.PROCESSED)).toBeTruthy()
    expect(within(rows[1] as HTMLElement).getByText(esMX.transactions.status.PARKED)).toBeTruthy()
    expect(screen.getByRole('link', { name: text.viewAll }).getAttribute('href')).toBe('/transactions?party_id=p1')
  })

  it('says so when the party has no transactions', async () => {
    renderApp(<PartyTransactionsCard partyId="p1" />, {
      path: '/parties/p1',
      routes: { [LIST]: () => json({ items: [], next_cursor: null }) },
    })

    expect(await screen.findByText(text.empty)).toBeTruthy()
  })
})
