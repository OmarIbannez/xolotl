// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BehaviourProfileResponse, CustomerRelationshipResponse, Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { BehaviourProfileCard } from './BehaviourProfileCard'
import { customerRelationship } from './fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.parties.profile
const PROFILES = 'GET /v1/monitoring/profiles/r1'

const DECLARED = {
  monthly_inflow_minor: 1000000,
  monthly_outflow_minor: 0,
  currency: 'MXN',
  expected_countries: ['MX'],
  expected_instruments: ['TRANSFER'],
}

function profile(month: string, overrides: Partial<BehaviourProfileResponse> = {}): BehaviourProfileResponse {
  return {
    month,
    metrics: {
      inflow_minor: '500000',
      outflow_minor: '0',
      inflow_count: 2,
      outflow_count: 0,
      cash_share: '0.1234',
      international_share: '0.0000',
      crypto_share: '0.0000',
      distinct_counterparties: 1,
      active_days: 2,
      currency: 'MXN',
    },
    declared_profile: null,
    deviation: null,
    deviated: false,
    computed_at: `${month}-28T09:00:00Z`,
    ...overrides,
  }
}

/** Twelve months newest first, as the API answers: 2026-09 back to 2025-10. */
const YEAR: BehaviourProfileResponse[] = Array.from({ length: 12 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 8 - index, 1))
  return profile(`${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`)
})

const FLAGGED = profile('2026-09', {
  metrics: { ...YEAR[0]?.metrics, inflow_minor: '2500050', outflow_minor: '30000', outflow_count: 1 },
  declared_profile: DECLARED,
  deviation: { monthly_inflow: '2.5', monthly_outflow: null, reasons: { monthly_outflow: 'DECLARED_ZERO' } },
  deviated: true,
})

function setup(
  roles: Role[] = ['ANALYST'],
  { rows = YEAR, relationship = customerRelationship(), routes = {} }: { rows?: BehaviourProfileResponse[]; relationship?: CustomerRelationshipResponse; routes?: FakeRoutes } = {},
) {
  return renderApp(<BehaviourProfileCard relationship={relationship} />, {
    me: makeMe({ roles }),
    routes: { [PROFILES]: () => json(rows), ...routes },
  })
}

const table = () => screen.findByRole('table', { name: text.table.label })

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('BehaviourProfileCard', () => {
  it('shows the last twelve months, newest first, in the chart and the table', async () => {
    setup()

    const rows = within(await table()).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(12)
    expect(rows[0]?.textContent).toContain('2026')
    expect(rows[11]?.textContent).toContain('2025')
    expect(within(rows[0] as HTMLElement).getByText('5,000.00 MXN')).toBeTruthy()
    expect(within(rows[0] as HTMLElement).getByText('12.34 %')).toBeTruthy()
    const chart = screen.getByRole('img', { name: /2025.*2026/ })
    expect(chart.querySelectorAll('[data-bar]')).toHaveLength(24)
    // The chart's numbers are in the metrics table, which describes it.
    const described = document.getElementById(chart.getAttribute('aria-describedby') ?? '')
    expect(described?.contains(screen.getByRole('table', { name: text.table.label }))).toBe(true)
    expect(screen.getByText(text.declared.none)).toBeTruthy()
  })

  it('marks the deviated month and explains a declared zero', async () => {
    setup(['ANALYST'], { rows: [FLAGGED, ...YEAR.slice(1)], relationship: customerRelationship({ declared_profile: DECLARED }) })

    const [flagged, quiet] = within(await table()).getAllByRole('row').slice(1)
    const month = within(flagged as HTMLElement)
    expect(month.getByText(text.deviated)).toBeTruthy()
    expect(month.getByText(text.deviation.inflowRatio.replace('{{ratio}}', '×2.5'))).toBeTruthy()
    expect(month.getByText(text.deviation.outflowDeclaredZero)).toBeTruthy()
    expect(within(quiet as HTMLElement).queryByText(text.deviated)).toBeNull()
    expect(within(quiet as HTMLElement).getByText(text.deviation.none)).toBeTruthy()
    // The declaration of the month is drawn as a dashed line, and listed above the chart.
    expect(screen.getByRole('img').querySelectorAll('[data-declared]').length).toBeGreaterThan(0)
    expect(screen.getByText('10,000.00 MXN')).toBeTruthy()
  })

  it('shows the empty state before any month is computed', async () => {
    setup(['ANALYST'], { rows: [] })

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.queryByRole('table', { name: text.table.label })).toBeNull()
  })

  it('offers the declared profile edit to writers only', async () => {
    setup(['AUDITOR_RO'])
    await table()
    expect(screen.queryByRole('button', { name: text.edit })).toBeNull()
  })

  it('lets a writer open the dialog and remove a declaration after confirming', async () => {
    const { user, fakeApi } = setup(['ANALYST'], {
      relationship: customerRelationship({ declared_profile: DECLARED }),
      routes: { 'PATCH /v1/relationships/r1': () => json(customerRelationship()) },
    })
    await table()

    await user.click(screen.getByRole('button', { name: text.edit }))
    expect(await screen.findByRole('dialog', { name: text.dialog.title })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: esMX.common.cancel }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    await user.click(screen.getByRole('button', { name: text.clear }))
    const confirm = await screen.findByRole('dialog')
    await user.click(within(confirm).getByRole('button', { name: text.clear }))

    await waitFor(() => expect(fakeApi.calls('PATCH /v1/relationships/r1')).toHaveLength(1))
    expect(await fakeApi.calls('PATCH /v1/relationships/r1')[0]?.json()).toEqual({ declared_profile: null })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.cleared))
  })

  it('shows a failed load with a retry', async () => {
    setup(['ANALYST'], { routes: { [PROFILES]: () => problem(500, 'INTERNAL') } })

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
  })
})
