import type { PartyPiiResponse, PartyResponse, PartyStaffResponse } from '@/api/types'

// Test data for the party pages: what the API answers, masked unless the name says otherwise.

export const RFC = 'CACX7605101P8'

export function party(overrides: Partial<PartyResponse> = {}): PartyResponse {
  return {
    id: 'p1',
    party_type: 'PERSON',
    country_code: 'MX',
    status: 'ACTIVE',
    display_name: 'A.M.P.G.',
    identifiers: [
      { id: 'i1', id_type: 'RFC', country_code: 'MX', masked_value: '****01P8', valid_from: null, valid_to: null },
    ],
    legal_hold: false,
    retention_until: null,
    created_at: '2026-10-18T12:00:00Z',
    updated_at: '2026-10-18T12:00:00Z',
    ...overrides,
  }
}

export function staffView(overrides: Partial<PartyStaffResponse> = {}): PartyStaffResponse {
  return {
    ...party(),
    person: null,
    organization: null,
    contacts: [
      { id: 'c1', kind: 'EMAIL', masked_value: '****a.mx', is_primary: true, created_at: '2026-10-18T12:00:00Z' },
    ],
    addresses: [],
    relationships: [],
    documents: [],
    customer_relationships: [],
    ...overrides,
  }
}

/** The plaintext the reveal returns. */
export function pii(overrides: Partial<PartyPiiResponse> = {}): PartyPiiResponse {
  return {
    party_id: 'p1',
    given_names: 'Ana María',
    first_surname: 'Pérez',
    second_surname: 'Gómez',
    birth_date: '1976-05-10',
    clave_elector: null,
    legal_name: null,
    identifiers: [{ id: 'i1', id_type: 'RFC', value: RFC }],
    contacts: [{ id: 'c1', kind: 'EMAIL', value: 'ana@acme.mx' }],
    addresses: [],
    ...overrides,
  }
}
