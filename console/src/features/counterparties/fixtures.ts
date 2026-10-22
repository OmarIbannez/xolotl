import type { CounterpartyDetail, CounterpartyResponse, RevealedCounterparty } from '@/api/types'
import { maskedSubject, matchSummary } from '@/features/screening/fixtures'

/** A counterparty as the Contrapartes list shows it: masked, pending review. */
export function counterpartyRow(overrides: Partial<CounterpartyResponse> = {}): CounterpartyResponse {
  return {
    id: 'c1',
    kind: 'PERSON',
    has_name: true,
    has_account: true,
    has_wallet: false,
    has_identifier: true,
    institution: 'BBVA',
    country: 'MX',
    wallet_network: null,
    screening_status: 'PENDING_REVIEW',
    txn_count: 3,
    initials: 'R.A.P.',
    account_masked: '****7897',
    wallet_masked: null,
    identifier_masked: '****1AAA',
    identifier_type: 'RFC',
    screened_at: '2026-12-08T10:00:00Z',
    subject_id: 's9',
    first_seen_at: '2026-12-07T09:00:00Z',
    last_seen_at: '2026-12-09T09:00:00Z',
    ...overrides,
  }
}

/** The detail of `counterpartyRow()`: one open finding and one run. */
export function counterpartyDetail(overrides: Partial<CounterpartyDetail> = {}): CounterpartyDetail {
  return {
    ...counterpartyRow(),
    findings: [
      matchSummary({
        id: 'm7',
        subject_id: 's9',
        party_id: null,
        counterparty_id: 'c1',
        subject: maskedSubject({ kind: 'COUNTERPARTY', party_id: null, counterparty_id: 'c1', initials: 'R.A.P.', identifiers: [] }),
      }),
    ],
    runs: [
      { id: 'r9abcdef-0000', subject_id: 's9', party_id: null, mode: 'COUNTERPARTY', outcome: 'PENDING_REVIEW', created_at: '2026-12-08T10:00:00Z' },
    ],
    ...overrides,
  }
}

/** The plaintext of `counterpartyRow()`, as the audited reveal answers it. */
export function revealedCounterparty(overrides: Partial<RevealedCounterparty> = {}): RevealedCounterparty {
  return {
    name: 'Rodrigo Alarcón Palomares',
    account: '012180001234567897',
    wallet_address: null,
    wallet_network: null,
    identifier_type: 'RFC',
    identifier: 'AAPR800101AAA',
    ...overrides,
  }
}
