import type {
  CounterpartySummary,
  ParkedInfo,
  RevealedCounterparty,
  TransactionRevealedView,
  TransactionStaffView,
  UnitAmountView,
} from '@/api/types'

export const PARTY_ID = '0191d3a2-6b1c-7000-8000-0000000000b1'

/** A masked counterparty: a person with a name and an account on record at a Mexican bank. */
export function counterpartySummary(overrides: Partial<CounterpartySummary> = {}): CounterpartySummary {
  return {
    id: 'cp1',
    kind: 'PERSON',
    has_name: true,
    has_account: true,
    has_wallet: false,
    has_identifier: false,
    institution: 'BANORTE',
    country: 'MX',
    wallet_network: null,
    screening_status: 'CLEAR',
    txn_count: 3,
    ...overrides,
  }
}

export function unitAmount(overrides: Partial<UnitAmountView> = {}): UnitAmountView {
  return {
    key: 'UMA/LAST_DAY_PREV_MONTH',
    unit: 'UMA',
    rule: 'LAST_DAY_PREV_MONTH',
    amount: '107.5268',
    unit_value_id: 41,
    resolved_date: '2026-09-30',
    ...overrides,
  }
}

export function parkedInfo(overrides: Partial<ParkedInfo> = {}): ParkedInfo {
  return {
    reason: 'UNIT_VALUE_MISSING',
    unit_code: 'UDI',
    needed_on: '2026-10-19',
    attempts: 2,
    parked_at: '2026-10-19T15:00:00Z',
    last_error: null,
    ...overrides,
  }
}

/** A processed SPEI transfer out of 12,500.00 MXN, as staff see it. */
export function transactionStaffView(overrides: Partial<TransactionStaffView> = {}): TransactionStaffView {
  return {
    id: 't1',
    external_id: 'TX-0001',
    occurred_at: '2026-10-19T16:30:00Z',
    status: 'PROCESSED',
    late: false,
    received_at: '2026-10-19T16:31:00Z',
    batch_id: 'b1',
    party_id: PARTY_ID,
    relationship_id: 'r1',
    account_id: 'a1',
    account_ref: 'ACC-77',
    product: 'EMONEY',
    direction: 'OUT',
    rail: 'SPEI',
    instrument: 'TRANSFER',
    channel: 'APP',
    amount_minor: 1_250_000,
    currency: 'MXN',
    fee_minor: null,
    asset_qty: null,
    asset_code: null,
    unit_amounts: [
      unitAmount({ key: 'MXN/SAME_DAY', unit: 'MXN', rule: 'SAME_DAY', amount: '12500.00', unit_value_id: null, resolved_date: '2026-10-19' }),
      unitAmount(),
    ],
    period_keys: { DAY: '2026-10-19', MONTH: '2026-10', QUARTER: '2026-Q4', BUSINESS_DAY: true },
    counterparty: counterpartySummary(),
    counterparty_screening_status_at_processing: 'CLEAR',
    device_present: true,
    geo_country: 'MX',
    geo_admin_area: 'CMX',
    crypto: null,
    fx: null,
    purpose_code: null,
    tier: 'N2',
    risk_rating: 'LOW',
    monitoring_profile: 'STANDARD',
    policy_snapshot_id: 'snap-1',
    processed_at: '2026-10-19T16:31:05Z',
    decision_id: null,
    parked: null,
    ...overrides,
  }
}

export function revealedCounterparty(overrides: Partial<RevealedCounterparty> = {}): RevealedCounterparty {
  return {
    name: 'María López Ruiz',
    account: '072180001234567890',
    wallet_address: null,
    wallet_network: null,
    identifier_type: null,
    identifier: null,
    ...overrides,
  }
}

/** `transactionStaffView()` revealed: the payload as the client sent it and the counterparty in plaintext. */
export function transactionRevealedView(overrides: Partial<TransactionRevealedView> = {}): TransactionRevealedView {
  return {
    ...transactionStaffView(),
    counterparty_revealed: revealedCounterparty(),
    raw: { external_id: 'TX-0001', amount: '12500.00', note: 'renta octubre' },
    ...overrides,
  }
}
