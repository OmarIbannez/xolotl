import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/errors'
import type { PersonAttributes } from '@/api/types'
import {
  addressSchema,
  contactSchema,
  customerSchema,
  documentProblem,
  editOrganizationSchema,
  editPersonSchema,
  emptyAddress,
  identifierSchema,
  MAX_DOCUMENT_BYTES,
  organizationFormOf,
  personFormOf,
  relationshipSchema,
  showFieldProblems,
  toAddressRequest,
  toContactRequest,
  toIdentifierRequest,
  toOrganizationUpdate,
  toPersonUpdate,
  toRelationshipOpenRequest,
  toRelationshipRequest,
  RELATIONSHIP_FIELD_OF,
} from './sub-forms'

const OTHER = '0191d3a2-6b1c-7000-8000-0000000000b2'

/** The issue messages (`validation.*` keys) of a failed parse, by path. */
function issues(result: { success: boolean; error?: { issues: { path: PropertyKey[]; message: string }[] } }) {
  return Object.fromEntries((result.error?.issues ?? []).map((issue) => [issue.path.join('.'), issue.message]))
}

describe('identifier, contact and address mappers', () => {
  it('maps an identifier, trimming it and defaulting the country to MX', () => {
    const values = identifierSchema.parse({ idType: 'CURP', value: '  PEGA760510HDFRMN09 ' })
    expect(toIdentifierRequest(values)).toEqual({ id_type: 'CURP', value: 'PEGA760510HDFRMN09', country_code: 'MX' })
    expect(issues(identifierSchema.safeParse({ idType: 'RFC', value: '', countryCode: 'MX' }))).toEqual({ value: 'required' })
  })

  it('maps a contact with its primary flag', () => {
    const values = contactSchema.parse({ kind: 'PHONE', value: '+52 55 1234 5678', isPrimary: true })
    expect(toContactRequest(values)).toEqual({ kind: 'PHONE', value: '+52 55 1234 5678', is_primary: true })
  })

  it('maps an address into its lines and codes, leaving out what was not given', () => {
    const values = addressSchema.parse({ ...emptyAddress(), street: 'Av. Reforma', extNumber: '222', postalCode: '06600' })
    expect(toAddressRequest(values)).toEqual({
      address_type: 'RESIDENTIAL',
      lines: { street: 'Av. Reforma', ext_number: '222' },
      country_code: 'MX',
      postal_code: '06600',
    })
    expect(issues(addressSchema.safeParse({ ...emptyAddress(), street: 'x', validFrom: '2026-1-1' }))).toEqual({
      validFrom: 'dateFormat',
    })
  })
})

describe('the relationship form', () => {
  const base = { toPartyId: OTHER, relType: 'SHAREHOLDER', ownershipPct: '', controlBasis: '', validFrom: '' } as const

  it('sends the ownership as a decimal string, never required', () => {
    expect(toRelationshipRequest(relationshipSchema.parse({ ...base, ownershipPct: '25.5' }))).toEqual({
      to_party_id: OTHER,
      rel_type: 'SHAREHOLDER',
      ownership_pct: '25.5',
    })
    expect(toRelationshipRequest(relationshipSchema.parse(base))).toEqual({ to_party_id: OTHER, rel_type: 'SHAREHOLDER' })
  })

  it.each(['0', '100.5', '12.12345', '-1', 'abc'])('refuses the ownership %s', (ownershipPct) => {
    expect(issues(relationshipSchema.safeParse({ ...base, ownershipPct }))).toEqual({ ownershipPct: 'ownership' })
  })

  it('accepts 100 and refuses any ownership for a type other than SHAREHOLDER', () => {
    expect(relationshipSchema.safeParse({ ...base, ownershipPct: '100' }).success).toBe(true)
    expect(issues(relationshipSchema.safeParse({ ...base, relType: 'DIRECTOR', ownershipPct: '10' }))).toEqual({
      ownershipPct: 'ownership',
    })
    expect(relationshipSchema.safeParse({ ...base, relType: 'DIRECTOR' }).success).toBe(true)
  })

  it('wants the other party as a uuid', () => {
    expect(issues(relationshipSchema.safeParse({ ...base, toPartyId: 'p2' }))).toEqual({ toPartyId: 'uuid' })
  })

  it('points a nested server error at its input', () => {
    const set: [string, string][] = []
    const error = new ApiError({
      status: 422,
      code: 'VALIDATION_FAILED',
      detail: 'x',
      errors: [{ loc: ['body', 'ownership_pct'], msg: 'too many', type: 'decimal_max_places' }],
    })
    expect(showFieldProblems(error, RELATIONSHIP_FIELD_OF, (name, { message }) => set.push([name, message]))).toBe(true)
    expect(set).toEqual([['ownershipPct', 'invalid']])
  })
})

describe('the attribute edit', () => {
  const person = {
    birth_country: 'MX',
    birth_state: null,
    nationalities: ['MX'],
    gender: null,
    occupation_code: null,
    economic_activity_code: null,
    is_resident: null,
    pep_status: 'NONE',
    pep_since: null,
    pep_until: null,
  } satisfies PersonAttributes

  it('patches only the fields that changed, clearing an emptied one with null', () => {
    const before = personFormOf(person)
    const after = editPersonSchema.parse({ ...before, pepStatus: 'DOMESTIC', nationalities: 'MX, US', birthCountry: '' })
    expect(toPersonUpdate(before, after)).toEqual({
      person: { pep_status: 'DOMESTIC', nationalities: ['MX', 'US'], birth_country: null },
    })
    expect(toPersonUpdate(before, before)).toBeNull()
  })

  it('maps residency and refuses a malformed nationality', () => {
    const before = personFormOf(person)
    expect(before.isResident).toBe('UNKNOWN')
    expect(toPersonUpdate(before, { ...before, isResident: 'NO' })).toEqual({ person: { is_resident: false } })
    expect(issues(editPersonSchema.safeParse({ ...before, nationalities: 'mex' }))).toEqual({ nationalities: 'countryCode' })
  })

  it('patches an organisation the same way', () => {
    const before = organizationFormOf({
      trade_name: 'Acme',
      legal_form: null,
      incorporation_date: null,
      industry_code: null,
      registry_ref: null,
      country_of_incorporation: null,
    })
    const after = editOrganizationSchema.parse({ ...before, tradeName: 'Acme Pagos', incorporationDate: '2010-02-03' })
    expect(toOrganizationUpdate(before, after)).toEqual({
      organization: { trade_name: 'Acme Pagos', incorporation_date: '2010-02-03' },
    })
  })
})

describe('the customer relationship form', () => {
  it('opens with the party, the external reference and the product, without an empty tier', () => {
    const values = customerSchema.parse({ partyId: 'p1', externalRef: ' CTA-1 ', product: 'CREDITO', accountTier: '' })
    expect(toRelationshipOpenRequest(values)).toEqual({ party_id: 'p1', external_ref: 'CTA-1', product: 'CREDITO' })
  })
})

describe('the document checks', () => {
  it('refuses a type the API does not take, an empty file and one over 25 MB', () => {
    expect(MAX_DOCUMENT_BYTES).toBe(25 * 1024 * 1024)
    expect(documentProblem({ type: 'application/pdf', size: 3 })).toBeNull()
    expect(documentProblem({ type: 'image/png', size: MAX_DOCUMENT_BYTES })).toBeNull()
    expect(documentProblem({ type: 'image/gif', size: 3 })).toBe('fileType')
    expect(documentProblem({ type: 'application/pdf', size: 0 })).toBe('required')
    expect(documentProblem({ type: 'image/jpeg', size: MAX_DOCUMENT_BYTES + 1 })).toBe('fileTooLarge')
  })
})
