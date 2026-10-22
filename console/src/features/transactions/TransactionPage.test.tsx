// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { parkedInfo, transactionRevealedView, transactionStaffView } from './fixtures'
import { TransactionPage } from './TransactionPage'

const text = esMX.transactions
const DETAIL = 'GET /v1/transactions/t1'
const INJECTION = '<img src=x onerror="window.__pwned=1">'

function setup(routes: FakeRoutes = {}) {
  return renderApp(
    <Routes>
      <Route path="/transactions/:transactionId" element={<TransactionPage />} />
    </Routes>,
    {
      path: '/transactions/t1',
      me: makeMe({ roles: ['ANALYST'] }),
      routes: {
        [DETAIL]: (request) =>
          new URL(request.url).searchParams.get('reveal') === 'true'
            ? json(transactionRevealedView({ raw: { note: INJECTION, amount: '12500.00' } }))
            : json(transactionStaffView()),
        ...routes,
      },
    },
  )
}

const rowsOf = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent))

const revealCalls = (requests: Request[]) =>
  requests.filter((request) => new URL(request.url).searchParams.get('reveal') === 'true')

describe('TransactionPage', () => {
  it('renders the staff view in its cards', async () => {
    setup()

    expect(await screen.findByRole('heading', { name: text.detail.title })).toBeTruthy()
    await screen.findByText('TX-0001')
    expect(screen.getByText(text.status.PROCESSED)).toBeTruthy()
    expect(screen.getByText(text.direction.OUT)).toBeTruthy()
    expect(screen.getByText(text.channel.APP)).toBeTruthy()
    expect(screen.getByText('12,500.00 MXN')).toBeTruthy()
    expect(screen.getByText('ACC-77')).toBeTruthy()
    expect(screen.getByText('snap-1')).toBeTruthy()
    // Period keys.
    expect(screen.getByText('2026-10')).toBeTruthy()
    expect(screen.getByText('2026-Q4')).toBeTruthy()
    // The counterparty, masked.
    const counterparty = screen.getByRole('region', { name: text.detail.counterparty.title })
    expect(within(counterparty).getByText(text.counterpartyKind.PERSON)).toBeTruthy()
    expect(within(counterparty).getByText('BANORTE')).toBeTruthy()
    expect(within(counterparty).getAllByText(text.screeningStatus.CLEAR).length).toBeGreaterThan(0)
    expect(within(counterparty).getByText(text.detail.counterparty.has_name)).toBeTruthy()
    expect(within(counterparty).getByText(text.detail.counterparty.has_account)).toBeTruthy()
    expect(within(counterparty).queryByText(text.detail.counterparty.has_wallet)).toBeNull()
    expect(screen.getByRole('region', { name: text.detail.raw.title })).toBeTruthy()
    expect(screen.queryByRole('region', { name: text.detail.parked.title })).toBeNull()
  })

  it('lists every unit amount with its unit value id and resolved date', async () => {
    setup()

    const table = await screen.findByRole('table', { name: text.detail.units.label })

    expect(rowsOf(table)).toEqual([
      ['MXN', 'SAME_DAY', '12500.00', '—', '2026-10-19'],
      ['UMA', 'LAST_DAY_PREV_MONTH', '107.5268', '41', '2026-09-30'],
    ])
  })

  it('shows no counterparty name or payload until "Revelar", then reveals both once and audited', async () => {
    const { user, fakeApi, queryClient } = setup()
    await screen.findByText('TX-0001')
    expect(document.body.textContent).not.toContain('María López Ruiz')
    expect(document.body.textContent).not.toContain('renta')
    expect(revealCalls(fakeApi.requests)).toHaveLength(0)

    const counterparty = screen.getByRole('region', { name: text.detail.counterparty.title })
    await user.click(within(counterparty).getByRole('button', { name: text.detail.reveal.button }))

    expect(await screen.findByText('María López Ruiz')).toBeTruthy()
    expect(screen.getByText('072180001234567890')).toBeTruthy()
    expect(screen.getAllByText(text.detail.revealNotice).length).toBeGreaterThan(0)
    expect(revealCalls(fakeApi.requests)).toHaveLength(1)
    // The payload renders as text: no markup from it reaches the page.
    const raw = screen.getByRole('region', { name: text.detail.raw.title })
    expect(within(raw).getByText(/window.__pwned/).tagName).toBe('PRE')
    expect(document.body.querySelector('img')).toBeNull()
    // Never a query: nothing a refetch could repeat or a cache could keep.
    expect(queryClient.getQueryCache().getAll().map((query) => JSON.stringify(query.queryKey))).not.toContainEqual(
      expect.stringContaining('reveal'),
    )

    await user.click(within(raw).getByRole('button', { name: text.detail.reveal.hide }))

    expect(document.body.textContent).not.toContain('María López Ruiz')
    expect(document.body.textContent).not.toContain('__pwned')
    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll().filter((mutation) => mutation.state.data !== undefined)).toEqual([]),
    )
    expect(revealCalls(fakeApi.requests)).toHaveLength(1)
  })

  it('shows why a parked transaction waits', async () => {
    setup({ [DETAIL]: () => json(transactionStaffView({ status: 'PARKED', parked: parkedInfo() })) })

    const parked = await screen.findByRole('region', { name: text.detail.parked.title })

    expect(within(parked).getByText(text.parkReason.UNIT_VALUE_MISSING)).toBeTruthy()
    expect(within(parked).getByText('UDI')).toBeTruthy()
    expect(within(parked).getByText('2')).toBeTruthy()
  })

  it('says when there is no counterparty', async () => {
    setup({ [DETAIL]: () => json(transactionStaffView({ counterparty: null, counterparty_screening_status_at_processing: null })) })

    const counterparty = await screen.findByRole('region', { name: text.detail.counterparty.title })

    expect(within(counterparty).getByText(text.detail.counterparty.none)).toBeTruthy()
    expect(within(counterparty).queryByRole('button', { name: text.detail.reveal.button })).toBeNull()
  })

  it('renders NotFound on a 404', async () => {
    setup({ [DETAIL]: () => problem(404, 'NOT_FOUND') })

    expect(await screen.findByText(esMX.notFound.title)).toBeTruthy()
  })

  it('shows any other failure as an error with a retry', async () => {
    setup({ [DETAIL]: () => problem(500, 'INTERNAL') })

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
  })
})
