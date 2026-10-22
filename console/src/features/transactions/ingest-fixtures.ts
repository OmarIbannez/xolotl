import type { FileStatusResponse, TransactionBatchResponse, TransactionStaffView } from '@/api/types'

// Test data for the ingest panels, the parked and backfill tabs and the party card: what the API
// answers to staff, masked (the counterparty is a summary, never a value).

export function staffTransaction(overrides: Partial<TransactionStaffView> = {}): TransactionStaffView {
  return {
    id: 't1',
    external_id: 'ext-0001',
    occurred_at: '2026-12-07T15:30:00Z',
    status: 'PROCESSED',
    late: false,
    received_at: '2026-12-07T15:30:05Z',
    batch_id: 'b1',
    party_id: 'p1',
    relationship_id: 'r1',
    account_id: 'a1',
    account_ref: 'DEFAULT',
    product: 'EMONEY',
    direction: 'IN',
    rail: 'CASH',
    instrument: 'CASH',
    channel: 'BRANCH',
    amount_minor: 1250050,
    currency: 'MXN',
    fee_minor: null,
    asset_qty: null,
    asset_code: null,
    unit_amounts: [],
    period_keys: null,
    counterparty: null,
    counterparty_screening_status_at_processing: null,
    device_present: false,
    geo_country: null,
    geo_admin_area: null,
    crypto: null,
    fx: null,
    purpose_code: null,
    tier: null,
    risk_rating: null,
    monitoring_profile: 'STANDARD',
    policy_snapshot_id: null,
    processed_at: '2026-12-07T15:30:09Z',
    decision_id: null,
    parked: null,
    ...overrides,
  }
}

export function parkedTransaction(overrides: Partial<TransactionStaffView> = {}): TransactionStaffView {
  return staffTransaction({
    id: 't2',
    external_id: 'ext-0002',
    status: 'PARKED',
    processed_at: null,
    parked: {
      reason: 'UNIT_VALUE_MISSING',
      unit_code: 'UDI',
      needed_on: '2026-12-07',
      attempts: 3,
      parked_at: '2026-12-07T15:31:00Z',
      last_error: null,
    },
    ...overrides,
  })
}

export function backfillTransaction(overrides: Partial<TransactionStaffView> = {}): TransactionStaffView {
  return staffTransaction({
    id: 't3',
    external_id: 'ext-0003',
    status: 'BACKFILL',
    occurred_at: '2026-11-20T10:00:00Z',
    received_at: '2026-12-07T15:30:05Z',
    processed_at: null,
    ...overrides,
  })
}

export function fileStatus(overrides: Partial<FileStatusResponse> = {}): FileStatusResponse {
  return {
    file_id: 'f1',
    status: 'UPLOADED',
    rows_total: null,
    rows_accepted: 0,
    rows_duplicate: 0,
    rows_rejected: 0,
    uploaded_at: '2026-12-07T15:00:00Z',
    finished_at: null,
    last_error: null,
    ...overrides,
  }
}

export function transactionBatch(overrides: Partial<TransactionBatchResponse> = {}): TransactionBatchResponse {
  return {
    id: '0191d3a2-6b1c-7000-8000-0000000000b1',
    source: 'API',
    item_count: 12,
    accepted: 10,
    duplicates: 2,
    received_at: '2026-12-07T15:00:00Z',
    request_id: 'req-123',
    file_id: null,
    ...overrides,
  }
}
