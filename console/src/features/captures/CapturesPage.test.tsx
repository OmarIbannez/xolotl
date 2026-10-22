// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { toast } from 'sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { stubPointerCapture } from '@/test/select'
import { CapturesPage } from './CapturesPage'
import { captureLimit, closedPeriod, period, reportableEvent, SOURCE, tierLimit } from './fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.captures
const EFFECTIVE = 'GET /v1/monitoring/captures/effective'
const PERIODS = 'GET /v1/monitoring/captures/periods'
const CAPTURES = 'GET /v1/monitoring/captures'
const CLOSE = 'POST /v1/monitoring/captures/periods/p1/close'

/** September has ended and is still open; October is the current month; August is closed. */
const periods = [period({ id: 'p2', period_key: '2026-10', period_start: '2026-10-01', period_end: '2026-10-31' }), period(), closedPeriod()]

const base: FakeRoutes = {
  [EFFECTIVE]: () => json([captureLimit(), tierLimit()]),
  [PERIODS]: () => json({ items: periods, next_cursor: null }),
  [CAPTURES]: () => json({ items: [reportableEvent()], next_cursor: null }),
}

function setup(role: Role, routes: FakeRoutes = {}) {
  return renderApp(
    <Routes>
      <Route path="/rules/captures" element={<CapturesPage />} />
    </Routes>,
    { path: '/rules/captures', me: makeMe({ roles: [role] }), routes: { ...base, ...routes } },
  )
}

/** The table named `name` once its rows have arrived (the loading table is another element). */
async function loadedTable(name: string) {
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

async function rowOf(key: string) {
  const table = await loadedTable(text.period.label)
  const row = within(table).getByText(key).closest('tr')
  if (row === null) throw new Error(`no row for ${key}`)
  return within(row)
}

beforeEach(() => {
  // 22 October 2026, noon in Mexico City.
  vi.useFakeTimers({ now: new Date('2026-10-22T18:00:00Z'), toFake: ['Date'] })
  stubPointerCapture()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CapturesPage', () => {
  it('shows the captures and limits with provenance', async () => {
    const { fakeApi } = setup('ANALYST')

    expect(await screen.findByRole('heading', { name: text.title })).toBeTruthy()
    const table = await loadedTable(text.limits.label)
    const capture = within(within(table).getByText('ITF_ROR_CASH').closest('tr') as HTMLElement)
    expect(capture.getByText(text.kind.THRESHOLD)).toBeTruthy()
    expect(capture.getByText('7500 USD')).toBeTruthy()
    expect(capture.getByText(text.limits.rule.PRIOR_BANKING_DAY)).toBeTruthy()
    expect(capture.getByText(text.limits.periodOf.MONTH)).toBeTruthy()
    expect(capture.getByText('ITF_ROR')).toBeTruthy()
    expect(capture.getByText('DCG art. 58 LRITF')).toBeTruthy()
    expect(capture.getByText(esMX.policy.provenance.VERIFIED)).toBeTruthy()
    expect(capture.getByRole('link', { name: `${text.propose} ITF_ROR_CASH` }).getAttribute('href')).toBe(
      '/policy?capture=ITF_ROR_CASH',
    )

    const tier = within(screen.getByText('N1/monthly_ops').closest('tr') as HTMLElement)
    expect(tier.getByText(text.kind.TIER_LIMIT)).toBeTruthy()
    expect(tier.getByText('750 UDI')).toBeTruthy()
    expect(tier.getByText(esMX.policy.provenance.UNVERIFIED)).toBeTruthy()
    expect(tier.getByRole('link', { name: `${text.propose} N1/monthly_ops` }).getAttribute('href')).toBe(
      '/policy?limit=N1%2Fmonthly_ops',
    )

    expect(screen.getByText(text.limits.source.replace('{{source}}', SOURCE))).toBeTruthy()
    // The policy in force today, Mexico City.
    expect(fakeApi.queries(EFFECTIVE)[0]?.get('on')).toBe('2026-10-22')
  })

  it('says when no policy is in force', async () => {
    setup('ANALYST', { [EFFECTIVE]: () => problem(404, 'POLICY_NOT_ASSIGNED') })

    expect(await screen.findByText(text.limits.noPolicy)).toBeTruthy()
  })

  it('the close button is offered to the MLRO on ended periods only', async () => {
    let closed = false
    const { user, fakeApi } = setup('MLRO', {
      [PERIODS]: () =>
        json({ items: closed ? [periods[0], closedPeriod({ id: 'p1', period_key: '2026-09' }), periods[2]] : periods, next_cursor: null }),
      [CLOSE]: () => {
        closed = true
        return json(closedPeriod({ id: 'p1', period_key: '2026-09' }))
      },
    })

    const september = await rowOf('2026-09')
    expect((await rowOf('2026-10')).queryByRole('button', { name: text.close.action })).toBeNull()
    expect((await rowOf('2026-08')).queryByRole('button', { name: text.close.action })).toBeNull()

    await user.click(september.getByRole('button', { name: text.close.action }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/anula/)).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: text.close.action }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const [sent] = fakeApi.calls(CLOSE)
    expect(sent?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(toast.success).toHaveBeenCalledWith(text.close.done)
    await waitFor(async () => expect((await rowOf('2026-09')).getByText(text.status.CLOSED)).toBeTruthy())
  })

  it('offers no close to an analyst', async () => {
    setup('ANALYST')

    await rowOf('2026-09')
    expect(screen.queryByRole('button', { name: text.close.action })).toBeNull()
  })

  it('a not-ended close shows the problem', async () => {
    const { user } = setup('ADMIN', { [CLOSE]: () => problem(409, 'PERIOD_NOT_ENDED') })

    await user.click((await rowOf('2026-09')).getByRole('button', { name: text.close.action }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: text.close.action }))

    expect(await within(dialog).findByText(esMX.errors.PERIOD_NOT_ENDED)).toBeTruthy()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('the captures list follows the chosen period', async () => {
    const { user, fakeApi } = setup('AUDITOR_RO')

    const events = await loadedTable(text.events.label)
    expect(within(events).getByRole('link', { name: 't1' }).getAttribute('href')).toBe('/transactions/t1')
    expect(fakeApi.queries(CAPTURES)[0]?.get('period_key')).toBeNull()

    await user.click((await rowOf('2026-09')).getByRole('button', { name: text.events.show }))

    await waitFor(() => {
      const query = fakeApi.queries(CAPTURES).at(-1)
      expect(query?.get('period_key')).toBe('2026-09')
      expect(query?.get('capture_code')).toBe('ITF_ROR_CASH')
    })
    await user.click(await screen.findByRole('button', { name: text.events.all }))
    await waitFor(() => expect(fakeApi.queries(CAPTURES).at(-1)?.get('period_key')).toBeNull())
  })
})
