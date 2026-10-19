import type {
  AddressResponse,
  CustomerRelationshipResponse,
  DocumentResponse,
  PartyPiiResponse,
  PartyRelationshipResponse,
  PartyResponse,
  PartyStaffResponse,
} from '@/api/types'

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

export const OTHER_PARTY = '0191d3a2-6b1c-7000-8000-0000000000b2'

export function address(overrides: Partial<AddressResponse> = {}): AddressResponse {
  return {
    id: 'a1',
    address_type: 'FISCAL',
    country_code: 'MX',
    admin_area_code: 'CMX',
    municipality_code: '015',
    postal_code: '06600',
    valid_from: '2026-01-01',
    valid_to: null,
    ...overrides,
  }
}

export function relationship(overrides: Partial<PartyRelationshipResponse> = {}): PartyRelationshipResponse {
  return {
    id: 'r9',
    from_party_id: 'p1',
    to_party_id: OTHER_PARTY,
    rel_type: 'SHAREHOLDER',
    ownership_pct: '25.5000',
    control_basis: null,
    valid_from: '2026-01-01',
    valid_to: null,
    source_doc_id: null,
    ...overrides,
  }
}

export function documentRow(overrides: Partial<DocumentResponse> = {}): DocumentResponse {
  return {
    id: 'd1',
    party_id: 'p1',
    doc_type: 'PROOF_OF_ADDRESS',
    sha256: 'ab'.repeat(32),
    size: 123456,
    content_type: 'application/pdf',
    uploaded_by: 'staff:user-1',
    uploaded_at: '2026-10-18T12:00:00Z',
    ...overrides,
  }
}

export function customerRelationship(
  overrides: Partial<CustomerRelationshipResponse> = {},
): CustomerRelationshipResponse {
  return {
    id: 'r1',
    party_id: 'p1',
    external_ref: 'CTA-0001',
    product: 'CREDITO',
    status: 'ACTIVE',
    account_tier: null,
    next_review_at: null,
    started_at: '2026-10-18T12:00:00Z',
    ended_at: null,
    created_at: '2026-10-18T12:00:00Z',
    ...overrides,
  }
}

/** A staff view with one row in every collection. */
export function fullStaffView(overrides: Partial<PartyStaffResponse> = {}): PartyStaffResponse {
  return staffView({
    person: {
      birth_country: 'MX',
      birth_state: null,
      nationalities: ['MX'],
      gender: null,
      occupation_code: null,
      economic_activity_code: null,
      is_resident: true,
      pep_status: 'NONE',
      pep_since: null,
      pep_until: null,
    },
    addresses: [address()],
    relationships: [relationship()],
    documents: [documentRow()],
    customer_relationships: [customerRelationship()],
    ...overrides,
  })
}
