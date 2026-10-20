import type { TFunction } from 'i18next'
import { z } from 'zod'
import { ApiError, hasProblemCode, isApiError } from '@/api/errors'
import { fieldProblems } from '@/api/field-errors'
import type {
  AddressInput,
  AddressType,
  ContactInput,
  ContactKind,
  DocumentType,
  IdentifierInput,
  IdentifierType,
  OrganizationAttributes,
  PartyRelationshipInput,
  PartyUpdateRequest,
  PepStatus,
  PersonAttributes,
  RelationshipOpenRequest,
  RelationshipType,
} from '@/api/types'
import { msgKey, type ValidationKey } from '@/i18n/validation'
import { IDENTIFIER_TYPES } from './form'

// The forms of the party detail page: one per sub-resource, the attribute edit and the
// customer relationship. Bounds are the API's request models', so what a form lets through the
// API takes; an optional input left empty is not sent.

export const CONTACT_KINDS: readonly ContactKind[] = ['EMAIL', 'PHONE']
export const ADDRESS_TYPES: readonly AddressType[] = ['RESIDENTIAL', 'FISCAL', 'OPERATIONAL']
export const RELATIONSHIP_TYPES: readonly RelationshipType[] = [
  'SHAREHOLDER',
  'DIRECTOR',
  'LEGAL_REP',
  'ATTORNEY',
  'CONTROLLER',
]
export const PEP_STATUSES: readonly PepStatus[] = ['NONE', 'DOMESTIC', 'FOREIGN', 'RCA', 'INTL_ORG']
export const DOCUMENT_TYPES: readonly DocumentType[] = [
  'IDENTITY',
  'PROOF_OF_ADDRESS',
  'CONSTITUTIVE_ACT',
  'POWER_OF_ATTORNEY',
  'TAX_CERTIFICATE',
  'OTHER',
]

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const COUNTRY = /^[A-Z]{2}$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
/** Up to three integer digits and four decimals, as the API's `Decimal(decimal_places=4)`. */
const PERCENT = /^\d{1,3}(\.\d{1,4})?$/

const country = z.string().trim().regex(COUNTRY, msgKey('countryCode'))
const optionalCountry = z.union([z.literal(''), country])
const optionalDate = z.union([z.literal(''), z.string().regex(ISO_DATE, msgKey('dateFormat'))])
/** An optional text of at most `max` characters; `''` means "not given". */
const optionalText = (max: number) => z.string().trim().max(max, msgKey('tooLong'))

/** `value` when it is not empty: spread into a request so an empty input sends nothing. */
function given<K extends string>(key: K, value: string): Partial<Record<K, string>> {
  return value === '' ? {} : ({ [key]: value } as Record<K, string>)
}

/**
 * The API reports a nested field at `['body', 'lines', 'street']` or `['body', 'person', 'gender']`;
 * `fieldProblems` reads `['body', <field>]`. Keep the last name in the path so those reach their input.
 */
export function withLeafFields(error: unknown): unknown {
  if (!isApiError(error)) return error
  return new ApiError({
    status: error.status,
    code: error.code,
    detail: error.detail,
    requestId: error.requestId,
    errors: error.errors.map((problem) => {
      const leaf = problem.loc.findLast((part) => typeof part === 'string')
      return problem.loc[0] === 'body' && leaf !== undefined ? { ...problem, loc: ['body', leaf] } : problem
    }),
  })
}

/**
 * Point each field problem of a 422 at its input (`fieldOf` maps the API's name to the form's).
 * Returns whether any was shown; when none was, the form shows the failure as a whole.
 */
export function showFieldProblems<F extends string, N extends string>(
  error: unknown,
  fieldOf: Readonly<Record<F, N>>,
  setError: (name: N, error: { type: string; message: string }) => void,
): boolean {
  const problems = fieldProblems(withLeafFields(error), Object.keys(fieldOf) as F[])
  for (const { field, message } of problems) setError(fieldOf[field], { type: 'server', message })
  return problems.length > 0
}

/** The message a refused identifier gets, instead of the generic one for its status. */
export function identifierRefusal(error: unknown, t: TFunction): { status: number; message: string } | undefined {
  if (hasProblemCode(error, 'IDENTIFIER_EXISTS')) return { status: 409, message: t('parties.create.identifierExists') }
  if (hasProblemCode(error, 'IDENTIFIER_INVALID')) return { status: 422, message: t('parties.create.identifierInvalid') }
  return undefined
}

// ---------------------------------------------------------------------------
// Identifiers
// ---------------------------------------------------------------------------

export const identifierSchema = z.object({
  idType: z.enum(IDENTIFIER_TYPES as [IdentifierType, ...IdentifierType[]]),
  value: z.string().trim().min(1, msgKey('required')).max(64, msgKey('identifierValue')),
  countryCode: country.default('MX'),
})
export type IdentifierFormValues = z.input<typeof identifierSchema>

export function toIdentifierRequest(values: z.output<typeof identifierSchema>): IdentifierInput {
  return { id_type: values.idType, value: values.value, country_code: values.countryCode }
}

export const IDENTIFIER_FIELD_OF = { id_type: 'idType', value: 'value', country_code: 'countryCode' } as const

// ---------------------------------------------------------------------------
// Contacts
// ---------------------------------------------------------------------------

export const contactSchema = z.object({
  kind: z.enum(CONTACT_KINDS as [ContactKind, ...ContactKind[]]),
  value: z.string().trim().min(1, msgKey('required')).max(254, msgKey('tooLong')),
  isPrimary: z.boolean(),
})
export type ContactFormValues = z.input<typeof contactSchema>

export function toContactRequest(values: z.output<typeof contactSchema>): ContactInput {
  return { kind: values.kind, value: values.value, is_primary: values.isPrimary }
}

export const CONTACT_FIELD_OF = { kind: 'kind', value: 'value', is_primary: 'isPrimary' } as const

// ---------------------------------------------------------------------------
// Addresses
// ---------------------------------------------------------------------------

export const addressSchema = z.object({
  addressType: z.enum(ADDRESS_TYPES as [AddressType, ...AddressType[]]),
  street: z.string().trim().min(1, msgKey('required')).max(200, msgKey('tooLong')),
  extNumber: optionalText(20),
  intNumber: optionalText(20),
  neighbourhood: optionalText(120),
  city: optionalText(120),
  countryCode: country,
  adminAreaCode: optionalText(8),
  municipalityCode: optionalText(8),
  postalCode: optionalText(10),
  validFrom: optionalDate,
})
export type AddressFormValues = z.input<typeof addressSchema>

export function emptyAddress(): AddressFormValues {
  return {
    addressType: 'RESIDENTIAL',
    street: '',
    extNumber: '',
    intNumber: '',
    neighbourhood: '',
    city: '',
    countryCode: 'MX',
    adminAreaCode: '',
    municipalityCode: '',
    postalCode: '',
    validFrom: '',
  }
}

export function toAddressRequest(values: z.output<typeof addressSchema>): AddressInput {
  return {
    address_type: values.addressType,
    lines: {
      street: values.street,
      ...given('ext_number', values.extNumber),
      ...given('int_number', values.intNumber),
      ...given('neighbourhood', values.neighbourhood),
      ...given('city', values.city),
    },
    country_code: values.countryCode,
    ...given('admin_area_code', values.adminAreaCode),
    ...given('municipality_code', values.municipalityCode),
    ...given('postal_code', values.postalCode),
    ...given('valid_from', values.validFrom),
  }
}

export const ADDRESS_FIELD_OF = {
  street: 'street',
  ext_number: 'extNumber',
  int_number: 'intNumber',
  neighbourhood: 'neighbourhood',
  city: 'city',
  country_code: 'countryCode',
  admin_area_code: 'adminAreaCode',
  municipality_code: 'municipalityCode',
  postal_code: 'postalCode',
  valid_from: 'validFrom',
} as const

// ---------------------------------------------------------------------------
// Relationships between parties
// ---------------------------------------------------------------------------

/** A percentage the API takes: more than 0, at most 100, at most four decimals. */
function isOwnership(value: string): boolean {
  if (!PERCENT.test(value)) return false
  const pct = Number(value)
  return pct > 0 && pct <= 100
}

export const relationshipSchema = z
  .object({
    toPartyId: z.string().trim().regex(UUID, msgKey('uuid')),
    relType: z.enum(RELATIONSHIP_TYPES as [RelationshipType, ...RelationshipType[]]),
    ownershipPct: z.string().trim(),
    controlBasis: optionalText(200),
    validFrom: optionalDate,
  })
  .superRefine((values, ctx) => {
    // Ownership is never required, and only a shareholder holds any.
    if (values.ownershipPct === '') return
    if (values.relType !== 'SHAREHOLDER' || !isOwnership(values.ownershipPct)) {
      ctx.addIssue({ code: 'custom', path: ['ownershipPct'], message: msgKey('ownership') })
    }
  })
export type RelationshipFormValues = z.input<typeof relationshipSchema>

export function toRelationshipRequest(values: z.output<typeof relationshipSchema>): PartyRelationshipInput {
  return {
    to_party_id: values.toPartyId,
    rel_type: values.relType,
    // A decimal string, never a float, so the four decimals arrive as typed.
    ...given('ownership_pct', values.ownershipPct),
    ...given('control_basis', values.controlBasis),
    ...given('valid_from', values.validFrom),
  }
}

export const RELATIONSHIP_FIELD_OF = {
  to_party_id: 'toPartyId',
  rel_type: 'relType',
  ownership_pct: 'ownershipPct',
  control_basis: 'controlBasis',
  valid_from: 'validFrom',
} as const

// ---------------------------------------------------------------------------
// Editing a party's attributes (names, birth date and identifiers are not patchable)
// ---------------------------------------------------------------------------

/** What the residency select holds (a Radix select has no empty value). */
export const RESIDENCIES = ['UNKNOWN', 'YES', 'NO'] as const

export const editPersonSchema = z.object({
  pepStatus: z.enum(PEP_STATUSES as [PepStatus, ...PepStatus[]]),
  pepSince: optionalDate,
  pepUntil: optionalDate,
  isResident: z.enum(RESIDENCIES),
  /** Two-letter codes separated by commas or spaces, for example `MX, US`. */
  nationalities: z
    .string()
    .trim()
    .refine((value) => splitCodes(value).every((code) => COUNTRY.test(code)), msgKey('countryCode')),
  occupationCode: optionalText(16),
  economicActivityCode: optionalText(16),
  birthCountry: optionalCountry,
  birthState: optionalText(64),
  gender: optionalText(16),
})
export type EditPersonValues = z.output<typeof editPersonSchema>

export const editOrganizationSchema = z.object({
  tradeName: optionalText(300),
  legalForm: optionalText(64),
  incorporationDate: optionalDate,
  industryCode: optionalText(16),
  registryRef: optionalText(64),
  countryOfIncorporation: optionalCountry,
})
export type EditOrganizationValues = z.output<typeof editOrganizationSchema>

function splitCodes(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((code) => code.trim())
    .filter((code) => code !== '')
}

const text = (value: string | null) => value ?? ''

export function personFormOf(person: PersonAttributes): EditPersonValues {
  return {
    pepStatus: person.pep_status,
    pepSince: text(person.pep_since),
    pepUntil: text(person.pep_until),
    isResident: person.is_resident === null ? 'UNKNOWN' : person.is_resident ? 'YES' : 'NO',
    nationalities: person.nationalities.join(', '),
    occupationCode: text(person.occupation_code),
    economicActivityCode: text(person.economic_activity_code),
    birthCountry: text(person.birth_country),
    birthState: text(person.birth_state),
    gender: text(person.gender),
  }
}

export function organizationFormOf(organization: OrganizationAttributes): EditOrganizationValues {
  return {
    tradeName: text(organization.trade_name),
    legalForm: text(organization.legal_form),
    incorporationDate: text(organization.incorporation_date),
    industryCode: text(organization.industry_code),
    registryRef: text(organization.registry_ref),
    countryOfIncorporation: text(organization.country_of_incorporation),
  }
}

/** An empty input clears the attribute: the API takes `null` for that. */
const orNull = (value: string) => (value === '' ? null : value)

function personAttributes(values: EditPersonValues): Required<NonNullable<PartyUpdateRequest['person']>> {
  return {
    pep_status: values.pepStatus,
    pep_since: orNull(values.pepSince),
    pep_until: orNull(values.pepUntil),
    is_resident: values.isResident === 'UNKNOWN' ? null : values.isResident === 'YES',
    nationalities: splitCodes(values.nationalities),
    occupation_code: orNull(values.occupationCode),
    economic_activity_code: orNull(values.economicActivityCode),
    birth_country: orNull(values.birthCountry),
    birth_state: orNull(values.birthState),
    gender: orNull(values.gender),
  }
}

function organizationAttributes(
  values: EditOrganizationValues,
): Required<NonNullable<PartyUpdateRequest['organization']>> {
  return {
    trade_name: orNull(values.tradeName),
    legal_form: orNull(values.legalForm),
    incorporation_date: orNull(values.incorporationDate),
    industry_code: orNull(values.industryCode),
    registry_ref: orNull(values.registryRef),
    country_of_incorporation: orNull(values.countryOfIncorporation),
  }
}

/** The entries of `after` that differ from `before`. */
function changed<T extends object>(before: T, after: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(after).filter(
      ([key, value]) => JSON.stringify(value) !== JSON.stringify((before as Record<string, unknown>)[key]),
    ),
  ) as Partial<T>
}

/** A patch of only the person's attributes the form changed; `null` when it changed none. */
export function toPersonUpdate(before: EditPersonValues, after: EditPersonValues): PartyUpdateRequest | null {
  const person = changed(personAttributes(before), personAttributes(after))
  return Object.keys(person).length === 0 ? null : { person }
}

/** A patch of only the organisation's attributes the form changed; `null` when it changed none. */
export function toOrganizationUpdate(
  before: EditOrganizationValues,
  after: EditOrganizationValues,
): PartyUpdateRequest | null {
  const organization = changed(organizationAttributes(before), organizationAttributes(after))
  return Object.keys(organization).length === 0 ? null : { organization }
}

export const PERSON_FIELD_OF = {
  pep_status: 'pepStatus',
  pep_since: 'pepSince',
  pep_until: 'pepUntil',
  is_resident: 'isResident',
  nationalities: 'nationalities',
  occupation_code: 'occupationCode',
  economic_activity_code: 'economicActivityCode',
  birth_country: 'birthCountry',
  birth_state: 'birthState',
  gender: 'gender',
} as const

export const ORGANIZATION_FIELD_OF = {
  trade_name: 'tradeName',
  legal_form: 'legalForm',
  incorporation_date: 'incorporationDate',
  industry_code: 'industryCode',
  registry_ref: 'registryRef',
  country_of_incorporation: 'countryOfIncorporation',
} as const

// ---------------------------------------------------------------------------
// Customer relationships
// ---------------------------------------------------------------------------

export const customerSchema = z.object({
  partyId: z.string().min(1),
  externalRef: z.string().trim().min(1, msgKey('required')).max(100, msgKey('tooLong')),
  product: optionalText(32),
  accountTier: optionalText(32),
})
export type CustomerFormValues = z.input<typeof customerSchema>

export function toRelationshipOpenRequest(values: z.output<typeof customerSchema>): RelationshipOpenRequest {
  return {
    party_id: values.partyId,
    external_ref: values.externalRef,
    ...given('product', values.product),
    ...given('account_tier', values.accountTier),
  }
}

export const CUSTOMER_FIELD_OF = {
  external_ref: 'externalRef',
  product: 'product',
  account_tier: 'accountTier',
} as const

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

/** The API's limit (`MAX_DOCUMENT_BYTES` in `modules/party/documents.py`). */
export const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
export const ALLOWED_DOCUMENT_TYPES = ['application/pdf', 'image/jpeg', 'image/png'] as const

/** Why the API would refuse this file, checked before anything is sent; `null` when it would not. */
export function documentProblem(file: Pick<File, 'size' | 'type'>): ValidationKey | null {
  if (!(ALLOWED_DOCUMENT_TYPES as readonly string[]).includes(file.type)) return 'fileType'
  if (file.size === 0) return 'required'
  if (file.size > MAX_DOCUMENT_BYTES) return 'fileTooLarge'
  return null
}
