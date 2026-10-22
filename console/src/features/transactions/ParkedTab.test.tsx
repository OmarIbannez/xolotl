// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { parkedTransaction } from './ingest-fixtures'
import { ParkedTab } from './ParkedTab'

const text = esMX.transactions
const PARKED = 'GET /v1/transactions/parked'

describe('ParkedTab', () => {
  it('lists each parked row with its reason, unit, date needed and attempts', async () => {
    renderApp(<ParkedTab />, {
      path: '/transactions',
      me: makeMe({ roles: ['ANALYST'] }),
      routes: {
        [PARKED]: () =>
          json({
            items: [
              parkedTransaction(),
              parkedTransaction({
                id: 't4',
                external_id: 'ext-0004',
                parked: {
                  reason: 'POLICY_NOT_ASSIGNED',
                  unit_code: null,
                  needed_on: null,
                  attempts: 1,
                  parked_at: '2026-12-07T15:31:00Z',
                  last_error: null,
                },
              }),
            ],
            next_cursor: null,
          }),
      },
    })

    await screen.findByText('ext-0002')
    const table = screen.getByRole('table', { name: text.parked.label })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0] as HTMLElement).getByText('ext-0002')).toBeTruthy()
    expect(within(rows[0] as HTMLElement).getByText(text.parkReason.UNIT_VALUE_MISSING)).toBeTruthy()
    expect(within(rows[0] as HTMLElement).getByText('UDI')).toBeTruthy()
    expect(within(rows[0] as HTMLElement).getByText('3')).toBeTruthy()
    expect(within(rows[1] as HTMLElement).getByText(text.parkReason.POLICY_NOT_ASSIGNED)).toBeTruthy()
  })

  it('pages with the cursor', async () => {
    const { user, fakeApi } = renderApp(<ParkedTab />, {
      path: '/transactions',
      routes: {
        [PARKED]: (request) =>
          new URL(request.url).searchParams.get('cursor') === 'c2'
            ? json({ items: [parkedTransaction({ id: 't9', external_id: 'ext-0009' })], next_cursor: null })
            : json({ items: [parkedTransaction()], next_cursor: 'c2' }),
      },
    })
    await screen.findByText('ext-0002')

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByText('ext-0009')).toBeTruthy()
    await waitFor(() => expect(fakeApi.queries(PARKED).at(-1)?.get('cursor')).toBe('c2'))
  })

  it('says so when nothing is parked', async () => {
    renderApp(<ParkedTab />, { path: '/transactions', routes: { [PARKED]: () => json({ items: [], next_cursor: null }) } })

    expect(await screen.findByText(text.parked.empty)).toBeTruthy()
  })
})
