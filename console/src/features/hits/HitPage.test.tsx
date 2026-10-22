// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Link, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { hitDetail, hitReveal, hitTransaction } from './fixtures'
import { HitPage } from './HitPage'

const text = esMX.hits
const DETAIL = 'GET /v1/monitoring/hits/h1'
const REVEAL = 'GET /v1/monitoring/hits/h1/reveal'

function setup(routes: FakeRoutes = {}) {
  return renderApp(
    <>
      <Link to="/monitoring/hits/h2">next hit</Link>
      <Routes>
        <Route path="/monitoring/hits/:hitId" element={<HitPage />} />
      </Routes>
    </>,
    {
      path: '/monitoring/hits/h1',
      me: makeMe({ roles: ['ANALYST'] }),
      routes: {
        [DETAIL]: () => json(hitDetail()),
        [REVEAL]: () => json(hitReveal()),
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

describe('HitPage', () => {
  it('renders the sentence, aggregates and evidence transactions masked', async () => {
    setup()

    expect(await screen.findByRole('heading', { name: text.detail.title })).toBeTruthy()
    expect(await screen.findByText(hitDetail().sentence)).toBeTruthy()
    expect(screen.getByText('CASH_STRUCTURING')).toBeTruthy()
    expect(screen.getByText('RELATIONSHIP:rel1:2026-10-12T00:00:00+00:00')).toBeTruthy()
    expect(screen.getByText(text.detail.cooldownNone)).toBeTruthy()
    expect(screen.getByText(text.detail.undecided)).toBeTruthy()
    // The values the rule read, with their unit and window.
    const aggregates = screen.getByRole('table', { name: text.detail.aggregates })
    expect(rowsOf(aggregates)).toEqual([
      ['near_count', '3', '—', 'P7D'],
      ['near_sum', '11500.00', 'USD', 'P7D'],
    ])
    // The evidence transactions, the counterparty masked.
    const transactions = screen.getByRole('table', { name: text.detail.transactionsLabel })
    const rows = rowsOf(transactions)
    expect(rows).toHaveLength(3)
    expect(rows[0]?.[0]).toBe(text.role.TRIGGER)
    expect(rows[1]?.[0]).toBe(text.role.WINDOW)
    expect(rows[0]).toContain('4,000.00 USD')
    expect(rows[0]?.join(' ')).toContain('BANORTE')
    expect(rows[2]?.join(' ')).toContain(text.transactions.noCounterparty)
    expect(within(transactions).getAllByRole('link')[0]?.getAttribute('href')).toBe('/transactions/t3')
    // Nothing personal until "Revelar".
    expect(document.body.textContent).not.toContain('María López Ruiz')
    expect(document.body.textContent).not.toContain('Laura')
    // No disposition, no note (spec section 17).
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('the reveal is a mutation and is forgotten on hide', async () => {
    const { user, fakeApi, queryClient } = setup()
    await screen.findByText(hitDetail().sentence)
    expect(fakeApi.calls(REVEAL)).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: text.reveal.button }))

    expect(await screen.findByText('María López Ruiz')).toBeTruthy()
    expect(screen.getByText('Laura Ramírez Soto')).toBeTruthy()
    expect(screen.getByText('072180001234567890')).toBeTruthy()
    expect(screen.getByText(text.reveal.notice)).toBeTruthy()
    expect(fakeApi.calls(REVEAL).map((request) => request.cache)).toEqual(['no-store'])
    expect(queryClient.getQueryCache().getAll().map((query) => JSON.stringify(query.queryKey))).not.toContainEqual(
      expect.stringContaining('reveal'),
    )

    await user.click(screen.getByRole('button', { name: text.reveal.hide }))

    expect(document.body.textContent).not.toContain('María López Ruiz')
    expect(document.body.textContent).not.toContain('Laura')
    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll().filter((mutation) => mutation.state.data !== undefined)).toEqual([]),
    )
    expect(fakeApi.calls(REVEAL)).toHaveLength(1)
  })

  it('drops the reveal when another hit opens', async () => {
    const { user } = setup({
      'GET /v1/monitoring/hits/h2': () => json(hitDetail({ id: 'h2', rule_code: 'RAPID_PASS_THROUGH' })),
    })
    await screen.findByText(hitDetail().sentence)
    await user.click(screen.getByRole('button', { name: text.reveal.button }))
    await screen.findByText('María López Ruiz')

    await user.click(screen.getByRole('link', { name: 'next hit' }))

    expect(await screen.findByText('RAPID_PASS_THROUGH')).toBeTruthy()
    expect(document.body.textContent).not.toContain('María López Ruiz')
    expect(screen.getByRole('button', { name: text.reveal.button })).toBeTruthy()
  })

  it('shows the cooldown and the decision when there are', async () => {
    setup({
      [DETAIL]: () => json(hitDetail({ cooldown_until: '2999-01-01T00:00:00Z', decision_id: 'dec-1' })),
    })

    expect(await screen.findByText(/dec-1/)).toBeTruthy()
    expect(screen.getByText(/^Activo hasta/)).toBeTruthy()
  })

  it('pages every linked transaction when the detail shows only the newest', async () => {
    const { user, fakeApi } = setup({
      [DETAIL]: () => json(hitDetail({ transaction_count: 501 })),
      'GET /v1/monitoring/hits/h1/transactions': (request) =>
        new URL(request.url).searchParams.get('cursor') === 'c2'
          ? json({ items: [hitTransaction({ transaction_id: 't0', role: 'COOLDOWN' })], next_cursor: null })
          : json({ items: [hitTransaction({ transaction_id: 't9' })], next_cursor: 'c2' }),
    })

    const table = () => screen.getByRole('table', { name: text.detail.transactionsLabel })
    await waitFor(() => expect(within(table()).getAllByRole('link')[0]?.getAttribute('href')).toBe('/transactions/t9'))
    expect(screen.getByText(/501/)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    await waitFor(() =>
      expect(
        within(table()).getByText(text.role.COOLDOWN),
      ).toBeTruthy(),
    )
    expect(fakeApi.queries('GET /v1/monitoring/hits/h1/transactions').map((query) => query.get('cursor'))).toEqual([null, 'c2'])
  })

  it('shows "not found" for an unknown hit', async () => {
    setup({ [DETAIL]: () => problem(404, 'NOT_FOUND') })

    expect(await screen.findByText(esMX.notFound.title)).toBeTruthy()
  })
})
