import type { CaptureLimitView, CapturePeriodResponse, ReportableEventSummary } from '@/api/types'

export const SOURCE = 'MX/ITF-IFPE'
export const MISSING_ID = '0191d3a2-6b1c-7000-8000-00000000m001'
export const EXTRA_ID = '0191d3a2-6b1c-7000-8000-00000000e001'

/** A capture of the effective policy: ITF_ROR_CASH, one cash operation of 7,500 USD or more. */
export function captureLimit(overrides: Partial<CaptureLimitView> = {}): CaptureLimitView {
  return {
    code: 'ITF_ROR_CASH',
    kind: 'THRESHOLD',
    threshold: '7500',
    unit: 'USD',
    conversion_rule: 'PRIOR_BANKING_DAY',
    window: null,
    period: 'MONTH',
    report_code: 'ITF_ROR',
    legal_basis: 'DCG art. 58 LRITF',
    confidence: 'VERIFIED',
    source: SOURCE,
    ...overrides,
  }
}

/** A tier's monthly limit, as `/effective` lists it (no report code). */
export function tierLimit(overrides: Partial<CaptureLimitView> = {}): CaptureLimitView {
  return captureLimit({
    code: 'N1/monthly_ops',
    kind: 'TIER_LIMIT',
    threshold: '750',
    unit: 'UDI',
    conversion_rule: 'LAST_DAY_PREV_MONTH',
    period: 'MONTH',
    report_code: null,
    legal_basis: 'Disposiciones ITF, nivel 1',
    confidence: 'UNVERIFIED',
    ...overrides,
  })
}

/** September 2026 of ITF_ROR_CASH: ended, still open, two live events. */
export function period(overrides: Partial<CapturePeriodResponse> = {}): CapturePeriodResponse {
  return {
    id: 'p1',
    capture_code: 'ITF_ROR_CASH',
    report_code: 'ITF_ROR',
    period_key: '2026-09',
    period_start: '2026-09-01',
    period_end: '2026-09-30',
    status: 'OPEN',
    event_count: 2,
    reconciled_count: null,
    reconciled_at: null,
    discrepancies: null,
    closed_at: null,
    reopened_at: null,
    change_count: 0,
    ...overrides,
  }
}

/** A closed period whose close restored one event and voided another. */
export function closedPeriod(overrides: Partial<CapturePeriodResponse> = {}): CapturePeriodResponse {
  return period({
    id: 'p0',
    period_key: '2026-08',
    period_start: '2026-08-01',
    period_end: '2026-08-31',
    status: 'CLOSED',
    event_count: 3,
    reconciled_count: 3,
    reconciled_at: '2026-09-01T10:00:00Z',
    discrepancies: { missing: [MISSING_ID], extra: [EXTRA_ID] },
    closed_at: '2026-09-01T10:00:00Z',
    ...overrides,
  })
}

/** A captured cash operation of transaction t1. */
export function reportableEvent(overrides: Partial<ReportableEventSummary> = {}): ReportableEventSummary {
  return {
    id: 'e1',
    capture_code: 'ITF_ROR_CASH',
    report_code: 'ITF_ROR',
    capture_kind: 'THRESHOLD',
    transaction_id: 't1',
    occurred_at: '2026-09-14T18:30:00Z',
    party_id: 'party-1',
    relationship_id: 'rel-1',
    product: null,
    period_key: '2026-09',
    capture_group_id: null,
    status: 'CAPTURED',
    late: false,
    captured_at: '2026-09-14T18:31:00Z',
    ...overrides,
  }
}
