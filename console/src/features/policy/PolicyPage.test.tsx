// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AssignmentPage,
  EffectivePolicyResponse,
  OverridePage,
  PackVersionPage,
} from '@/api/types'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, pending, refuse, resetFakeApi, respond } from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { PolicyPage } from './PolicyPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const text = esMX.policy
const PACK_VERSION_ID = '0199f0a0-0000-7000-8000-000000000001'
const APPROVER = '0199f0a0-0000-7000-8000-0000000000aa'
const HASH = 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90'
const PENDING_BASIS = 'DCG art. 58 LRITF: granularidad mes/trimestre pendiente (spec §13)'

const verified = { legal_basis: 'DCG art. 58 LRITF', confidence: 'VERIFIED', verified_as_of: '2026-10-18' } as const

function effective(overrides: Partial<EffectivePolicyResponse> = {}): EffectivePolicyResponse {
  return {
    policy: {
      units: { quote_ccy: 'MXN', tier_limit_rule: { value: 'LAST_DAY_PREV_MONTH', ...verified } },
      kyc: {
        tiers: [
          {
            code: 'N1',
            limits: {
              monthly_ops: { value: { amount: '750', unit: 'UDI' }, ...verified },
              max_balance: { value: { amount: '1000', unit: 'UDI' }, ...verified },
            },
            required_fields: ['given_names'],
            required_checks: ['SCREENING'],
            assurance: 'A0',
          },
          {
            code: 'N2',
            limits: { monthly_ops: { value: { amount: '3000', unit: 'UDI' }, ...verified }, max_balance: null },
            required_fields: ['curp'],
            required_checks: ['CURP_VALIDATION'],
            assurance: 'A2',
          },
        ],
        review_frequency: { HIGH: { value: 'P6M', ...verified } },
        geolocation_required: { value: true, ...verified },
      },
      ubo: {
        ownership_threshold_pct: { value: '25', ...verified },
        control_tests: ['VOTING_RIGHTS'],
        fallback: 'SENIOR_MANAGING_OFFICIAL',
        legal_person_can_be_ubo: false,
      },
      screening: {
        mandatory_lists: [{ value: 'MX_LPB', ...verified }],
        recommended_lists: ['OFAC_SDN'],
        pep: { cooling_off: { DOMESTIC: { value: 'P1Y', ...verified } } },
        on_true_match: { MX_LPB: { action: 'SUSPEND_IMMEDIATELY', report: 'MX_F41_ROI24', notify_client: false } },
      },
      captures: [
        {
          code: 'ITF_ROR_CASH',
          kind: 'THRESHOLD',
          threshold: { value: { amount: '5000', unit: 'USD' }, ...verified },
          conversion_rule: 'PRIOR_BANKING_DAY',
          window: null,
          period: { value: 'QUARTER', legal_basis: PENDING_BASIS, confidence: 'UNVERIFIED', verified_as_of: '2026-10-18' },
          report: 'MX_F41_ROR',
          filing: 'SERIALIZED',
          merge_into: null,
        },
      ],
      reports: [],
      retention: {
        kyc_after_relationship: { value: 'P10Y', ...verified },
        transactions: { value: 'P10Y', ...verified },
      },
      governance: { committee_required: { value: true, ...verified }, four_eyes_on_reports: true },
      on: '2026-01-20',
      product: 'EMONEY',
      country: 'MX',
      pack_codes: ['MX/ITF-IFPE'],
      pack_version_ids: [PACK_VERSION_ID],
      override_version: 2,
    },
    snapshot_id: '0199f0a0-0000-7000-8000-0000000000ff',
    content_hash: HASH,
    converted: {
      'kyc.tiers[N1].limits.monthly_ops': {
        amount: '6390.00',
        currency: 'MXN',
        unit: 'UDI',
        rate: '8.52000000',
        unit_value_id: 7,
        rule: 'LAST_DAY_PREV_MONTH',
        resolved_date: '2025-12-31',
      },
      'kyc.tiers[N1].limits.max_balance': null,
      'kyc.tiers[N2].limits.monthly_ops': null,
      'captures[ITF_ROR_CASH].threshold': null,
    },
    missing_units: ['USD_MXN_FIX'],
    ...overrides,
  }
}

function packs(title = 'Instituciones de Tecnología Financiera — IFPE'): PackVersionPage {
  return {
    items: [
      {
        id: PACK_VERSION_ID,
        code: 'MX/ITF-IFPE',
        version: 1,
        title,
        effective_from: '2026-01-01',
        effective_to: null,
        content_hash: HASH,
        loaded_at: '2026-10-18T12:00:00Z',
      },
    ],
    next_cursor: null,
  }
}

function assignments(product: string | null = null, nextCursor: string | null = null): AssignmentPage {
  return {
    items: [
      {
        id: `assignment-${product ?? 'all'}`,
        pack_version_id: PACK_VERSION_ID,
        pack: 'MX/ITF-IFPE',
        version: 1,
        product,
        effective_from: '2026-01-01',
        effective_to: null,
        approved_by: APPROVER,
        created_at: '2026-10-18T12:00:00Z',
      },
    ],
    next_cursor: nextCursor,
  }
}

function overrides(): OverridePage {
  return {
    items: [
      {
        id: 'override-1',
        version: 2,
        overrides: { tier_limits: { N1: { monthly_ops: { amount: '500', unit: 'UDI' } } }, committee_required: true },
        effective_from: '2026-01-05',
        approved_by: APPROVER,
        created_at: '2026-10-18T12:00:00Z',
      },
    ],
    next_cursor: null,
  }
}

interface Served {
  policy?: () => ReturnType<typeof respond>
  packTitle?: string
  assignmentPage?: (cursor: unknown) => AssignmentPage
}

function serve({ policy = () => respond(effective()), packTitle, assignmentPage }: Served = {}) {
  fakeApi.GET.mockImplementation((path, init) => {
    if (path === '/policy/effective') return policy()
    if (path === '/policy/packs') return respond(packs(packTitle))
    if (path === '/policy/assignments') {
      return respond(assignmentPage ? assignmentPage(init?.params?.query?.cursor) : assignments())
    }
    if (path === '/policy/overrides') return respond(overrides())
    return refuse(404, 'NOT_FOUND')
  })
}

const effectiveCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/policy/effective')
const assignmentCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/policy/assignments')
const tiersTable = () => screen.findByRole('table', { name: text.tiers.title })
/** A paged table once its rows have arrived (the loading table is another element). */
async function loaded(name: string): Promise<HTMLElement> {
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

beforeEach(() => {
  resetFakeApi()
  vi.useFakeTimers({ now: new Date('2026-10-19T12:00:00Z'), toFake: ['Date'] })
  serve()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('PolicyPage: states', () => {
  it('shows a skeleton while the effective policy loads', () => {
    fakeApi.GET.mockImplementation(() => pending())
    renderWithProviders(<PolicyPage />)

    const busy = screen.getAllByRole('status').filter((el) => el.getAttribute('aria-busy') === 'true')
    expect(busy.length).toBeGreaterThan(0)
    // today is the default date
    expect(effectiveCalls()[0]?.[1]?.params?.query).toEqual({ on: '2026-10-19' })
  })

  it('says no pack is assigned on a 404 POLICY_NOT_ASSIGNED, as a notice and not an error', async () => {
    serve({ policy: () => refuse(404, 'POLICY_NOT_ASSIGNED') })
    renderWithProviders(<PolicyPage />)

    expect(await screen.findByText(text.notAssigned)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a failure with its reference and retries', async () => {
    serve({ policy: () => refuse(500, 'INTERNAL') })
    const user = userEvent.setup()
    renderWithProviders(<PolicyPage />)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.INTERNAL)
    expect(alert.textContent).toContain('req-test')

    serve()
    await user.click(within(alert).getByRole('button', { name: esMX.common.retry }))

    expect(await tiersTable()).toBeTruthy()
    expect(effectiveCalls()).toHaveLength(2)
  })
})

describe('PolicyPage: the effective policy', () => {
  it('renders the tiers with their limits and conversions, and the missing units', async () => {
    renderWithProviders(<PolicyPage />)

    const row = within(within(await tiersTable()).getByRole('row', { name: /N1/ }))
    expect(row.getByRole('cell', { name: '750 UDI' })).toBeTruthy()
    expect(row.getByRole('cell', { name: '1000 UDI' })).toBeTruthy()
    expect(row.getByText(/6,390\.00 MXN · UDI 2025-12-31/)).toBeTruthy()
    expect(row.getByRole('cell', { name: 'A0' })).toBeTruthy()

    const n2 = within(screen.getByRole('row', { name: /N2/ }))
    expect(n2.getByRole('cell', { name: '3000 UDI' })).toBeTruthy()

    const captures = within(screen.getByRole('table', { name: text.captures.title }))
    expect(captures.getByRole('cell', { name: '5000 USD' })).toBeTruthy()
    expect(captures.getByRole('cell', { name: 'PRIOR_BANKING_DAY' })).toBeTruthy()
    expect(captures.getByRole('cell', { name: 'MX_F41_ROR' })).toBeTruthy()

    expect(screen.getByText('MX_LPB')).toBeTruthy()
    expect(screen.getByText('OFAC_SDN')).toBeTruthy()
    expect(screen.getAllByText('P10Y')).toHaveLength(2)
    // the header card names the pack (the assignments table names it again)
    expect(screen.getAllByText('MX/ITF-IFPE').length).toBeGreaterThan(0)

    const snapshot = screen.getByText(HASH.slice(0, 12))
    expect(snapshot.getAttribute('title')).toBe(HASH)
    expect(snapshot.className).toContain('font-mono')

    const notice = screen.getByText((_, el) => el?.getAttribute('data-slot') === 'missing-units')
    expect(notice.textContent).toContain('USD_MXN_FIX')
  })

  it('hides the missing-units notice when every unit converted', async () => {
    serve({ policy: () => respond(effective({ missing_units: [] })) })
    renderWithProviders(<PolicyPage />)

    await tiersTable()
    expect(screen.queryByText(/USD_MXN_FIX/)).toBeNull()
  })

  it('marks an unverified value with a warning badge that carries its legal basis', async () => {
    renderWithProviders(<PolicyPage />)
    await tiersTable()

    const badge = screen.getByText(text.provenance.UNVERIFIED).closest('[data-variant]')
    expect(badge?.getAttribute('data-variant')).toBe('warning')
    expect(badge?.getAttribute('title')).toContain(PENDING_BASIS)
    expect(badge?.getAttribute('title')).toContain('2026-10-18')

    const good = screen.getAllByText(text.provenance.VERIFIED)[0]?.closest('[data-variant]')
    expect(good?.getAttribute('data-variant')).toBe('good')
  })

  it('asks again for the date and product the filter names', async () => {
    const user = userEvent.setup()
    renderWithProviders(<PolicyPage />)
    await tiersTable()

    fireEvent.change(screen.getByLabelText(text.filters.on), { target: { value: '2026-01-20' } })
    await user.type(screen.getByLabelText(text.filters.product), 'EMONEY')
    await user.click(screen.getByRole('button', { name: text.filters.apply }))

    await waitFor(() => expect(effectiveCalls()).toHaveLength(2))
    expect(effectiveCalls()[1]?.[1]?.params?.query).toEqual({ on: '2026-01-20', product: 'EMONEY' })
  })
})

describe('PolicyPage: assignments and overrides', () => {
  it('lists the assignments with their approver and pages through them', async () => {
    serve({
      assignmentPage: (cursor) => (cursor === undefined ? assignments(null, 'cursor-2') : assignments('CRYPTO')),
    })
    const user = userEvent.setup()
    renderWithProviders(<PolicyPage />)

    const table = await loaded(text.assignments.title)
    const row = within(within(table).getByRole('row', { name: /MX\/ITF-IFPE/ }))
    expect(row.getByText(text.assignments.allProducts)).toBeTruthy()
    expect(row.getByTitle(APPROVER)).toBeTruthy()

    const pager = within(table.closest('[data-slot="card"]') as HTMLElement).getByRole('navigation')
    await user.click(within(pager).getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByText('CRYPTO')).toBeTruthy()
    expect(assignmentCalls().map(([, init]) => init?.params?.query?.cursor)).toEqual([undefined, 'cursor-2'])
  })

  it('lists the overrides with the keys they set', async () => {
    renderWithProviders(<PolicyPage />)

    const table = await loaded(text.overrides.title)
    const row = within(within(table).getAllByRole('row')[1] as HTMLElement)
    expect(row.getByRole('cell', { name: '2' })).toBeTruthy()
    expect(row.getByText('tier_limits, committee_required')).toBeTruthy()
    expect(row.getByTitle(APPROVER)).toBeTruthy()
  })
})

it('shows a pack title as text, never markup', async () => {
  const MARKUP = '<img src=x onerror="window.__pwned=1">'
  serve({ packTitle: MARKUP })
  renderWithProviders(<PolicyPage />)

  expect(await screen.findByText(MARKUP)).toBeTruthy()
  expect(document.body.querySelector('img')).toBeNull()
  expect(Reflect.get(window, '__pwned')).toBeUndefined()
})

it('follows the language switcher', async () => {
  await i18n.changeLanguage('en')
  renderWithProviders(<PolicyPage />)

  expect(await screen.findByRole('heading', { name: en.policy.title, level: 1 })).toBeTruthy()
  expect(await screen.findByRole('table', { name: en.policy.tiers.title })).toBeTruthy()
  expect(screen.getAllByText(en.policy.provenance.VERIFIED).length).toBeGreaterThan(0)
  expect(screen.getByRole('button', { name: en.policy.filters.apply })).toBeTruthy()
})
