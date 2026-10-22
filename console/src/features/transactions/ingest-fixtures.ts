import type { FileStatusResponse, TransactionBatchResponse, TransactionStaffView } from '@/api/types'
import { parkedInfo, transactionStaffView } from './fixtures'

// Test data for the ingest panels, the parked and backfill tabs and the party card, built on the
// staff views of `./fixtures`: what the API answers to staff, masked.

/** A processed cash deposit of 12,500.50 MXN of party `p1`. */
export function staffTransaction(overrides: Partial<TransactionStaffView> = {}): TransactionStaffView {
  return transactionStaffView({
    id: 't1',
    external_id: 'ext-0001',
    occurred_at: '2026-12-07T15:30:00Z',
    received_at: '2026-12-07T15:30:05Z',
    party_id: 'p1',
    direction: 'IN',
    rail: 'CASH',
    instrument: 'CASH',
    channel: 'BRANCH',
    amount_minor: 1250050,
    currency: 'MXN',
    ...overrides,
  })
}

export function parkedTransaction(overrides: Partial<TransactionStaffView> = {}): TransactionStaffView {
  return staffTransaction({
    id: 't2',
    external_id: 'ext-0002',
    status: 'PARKED',
    processed_at: null,
    parked: parkedInfo({ needed_on: '2026-12-07', attempts: 3, parked_at: '2026-12-07T15:31:00Z' }),
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
