import type { HitDetail, HitReveal, HitSummary, HitTransactionView, PartyResponse } from '@/api/types'
import { counterpartySummary } from '@/features/transactions/fixtures'

export const HIT_PARTY_ID = '0191d3a2-6b1c-7000-8000-0000000000c1'

/** The party of a hit as the API masks it: initials and a masked identifier. */
export function maskedParty(overrides: Partial<PartyResponse> = {}): PartyResponse {
  return {
    id: HIT_PARTY_ID,
    party_type: 'PERSON',
    country_code: 'MX',
    status: 'ACTIVE',
    display_name: 'L.R.',
    identifiers: [
      {
        id: 'id1',
        id_type: 'RFC',
        country_code: 'MX',
        masked_value: '•••••••••123',
        valid_from: null,
        valid_to: null,
      },
    ],
    legal_hold: false,
    retention_until: null,
    created_at: '2026-10-01T12:00:00Z',
    updated_at: '2026-10-01T12:00:00Z',
    ...overrides,
  }
}

/** A cash structuring hit of medium severity, raised as an alert. */
export function hitSummary(overrides: Partial<HitSummary> = {}): HitSummary {
  return {
    id: 'h1',
    rule_code: 'CASH_STRUCTURING',
    rule_version_id: 'rv1',
    typology: ['STRUCTURING'],
    severity: 'MEDIUM',
    outcome: 'ALERT',
    key_kind: 'RELATIONSHIP',
    key: 'rel1',
    party: maskedParty(),
    relationship_id: 'rel1',
    window_start: '2026-10-12T00:00:00Z',
    window_end: '2026-10-19T00:00:00Z',
    fired_count: 3,
    cooldown_until: null,
    first_transaction_id: 't3',
    first_occurred_at: '2026-10-15T16:00:00Z',
    decision_id: null,
    created_at: '2026-10-15T16:00:05Z',
    ...overrides,
  }
}

/** A transaction behind a hit: a cash deposit of 4,000.00 USD with a masked counterparty. */
export function hitTransaction(overrides: Partial<HitTransactionView> = {}): HitTransactionView {
  return {
    transaction_id: 't3',
    occurred_at: '2026-10-15T16:00:00Z',
    role: 'TRIGGER',
    direction: 'IN',
    instrument: 'CASH',
    rail: 'CASH',
    amount_minor: 400_000,
    currency: 'USD',
    counterparty: counterpartySummary(),
    ...overrides,
  }
}

export function hitDetail(overrides: Partial<HitDetail> = {}): HitDetail {
  return {
    ...hitSummary(),
    evidence: {
      aggregates: {
        near_count: { value: '3', unit: null, window: 'P7D' },
        near_sum: { value: '11500.00', unit: 'USD', window: 'P7D' },
      },
      params: { threshold: '5000', near_ratio: '0.70', window: 'P7D', min_count: '3' },
      explanation: 'Dispara cuando el cliente recibe 3 o más depósitos en efectivo en 7 días',
      lists_hash: 'abc123',
    },
    transactions: [
      hitTransaction(),
      hitTransaction({ transaction_id: 't2', occurred_at: '2026-10-14T16:00:00Z', role: 'WINDOW', amount_minor: 380_000 }),
      hitTransaction({
        transaction_id: 't1',
        occurred_at: '2026-10-13T16:00:00Z',
        role: 'WINDOW',
        amount_minor: 370_000,
        counterparty: null,
      }),
    ],
    transaction_count: 3,
    assignment_id: 'as1',
    policy_snapshot_id: 'snap-1',
    definition_hash: 'd3f1a9',
    dedup_key: 'RELATIONSHIP:rel1:2026-10-12T00:00:00+00:00',
    sentence:
      'Dispara cuando el cliente recibe 3 o más depósitos en efectivo entre 70 % y 100 % del umbral en 7 días y su suma alcanza el umbral',
    ...overrides,
  }
}

/** The reveal of `hitDetail()`: the party's names and the one counterparty in plaintext. */
export function hitReveal(overrides: Partial<HitReveal> = {}): HitReveal {
  return {
    party: {
      party_id: HIT_PARTY_ID,
      given_names: 'Laura',
      first_surname: 'Ramírez',
      second_surname: 'Soto',
      birth_date: null,
      clave_elector: null,
      legal_name: null,
      identifiers: [],
      contacts: [],
      addresses: [],
    },
    counterparties: {
      cp1: {
        name: 'María López Ruiz',
        account: '072180001234567890',
        wallet_address: null,
        wallet_network: null,
        identifier_type: null,
        identifier: null,
      },
    },
    ...overrides,
  }
}
