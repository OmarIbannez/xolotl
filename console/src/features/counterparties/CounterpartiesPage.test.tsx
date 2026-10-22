// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { CounterpartiesPage } from './CounterpartiesPage'
import { counterpartyRow } from './fixtures'

const text = esMX.counterparties
const LIST = 'GET /v1/counterparties'

function setup(routes: FakeRoutes = {}) {
  return renderApp(<CounterpartiesPage />, {
    path: '/counterparties',
    me: makeMe({ roles: ['ANALYST'] }),
    routes: {
      [LIST]: (request) =>
        new URL(request.url).searchParams.get('status') === 'CLEAR'
          ? json({
              items: [counterpartyRow({ id: 'c2', initials: '', screening_status: 'CLEAR', account_masked: null, wallet_masked: '****mcfd', wallet_network: 'XBT' })],
              next_cursor: null,
            })
          : json({ items: [counterpartyRow()], next_cursor: 'next' }),
      ...routes,
    },
  })
}

async function loadedTable() {
  const name = text.table.label
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

describe('CounterpartiesPage', () => {
  it('opens on pending review and renders the masked rows', async () => {
    const { fakeApi } = setup()

    const table = await loadedTable()

    expect(screen.getByRole('heading', { name: text.title })).toBeTruthy()
    for (const tab of ['PENDING_REVIEW', 'TRUE_MATCH', 'CLEAR', 'UNSCREENED'] as const) {
      expect(screen.getByRole('tab', { name: text.tabs[tab] })).toBeTruthy()
    }
    expect(screen.getByRole('tab', { name: text.tabs.PENDING_REVIEW }).getAttribute('aria-selected')).toBe('true')
    const [query] = fakeApi.queries(LIST)
    expect(query?.get('status')).toBe('PENDING_REVIEW')
    expect(query?.get('limit')).toBe('20')
    const [, row] = within(table).getAllByRole('row')
    const cells = within(row as HTMLElement)
    expect(cells.getByRole('link', { name: 'R.A.P.' }).getAttribute('href')).toBe('/counterparties/c1')
    expect(cells.getByText(text.kind.PERSON)).toBeTruthy()
    expect(cells.getByText('BBVA')).toBeTruthy()
    expect(cells.getByText(`${text.table.accountPrefix} ****7897`)).toBeTruthy()
    expect(cells.getByText(text.status.PENDING_REVIEW)).toBeTruthy()
    expect(cells.getByText('3')).toBeTruthy()
    expect(screen.getByRole('button', { name: esMX.common.pager.next })).toBeTruthy()
  })

  it('switching tab asks for that status and shows a wallet-only row by its kind', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()

    await user.click(screen.getByRole('tab', { name: text.tabs.CLEAR }))

    await waitFor(() => expect(fakeApi.queries(LIST).at(-1)?.get('status')).toBe('CLEAR'))
    expect(await screen.findByRole('link', { name: text.unnamed })).toBeTruthy()
    expect(screen.getByText('XBT ****mcfd')).toBeTruthy()
    expect(fakeApi.queries(LIST).at(-1)?.get('cursor')).toBeNull()
  })

  it('shows the empty state', async () => {
    setup({ [LIST]: () => json({ items: [], next_cursor: null }) })

    expect(await screen.findByText(text.empty)).toBeTruthy()
  })
})
