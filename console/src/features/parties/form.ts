import { z } from 'zod'
import type { IdentifierType, PartyCreateRequest } from '@/api/types'
import { msgKey } from '@/i18n/validation'

/**
 * Every identifier type the API validates, in the order the console offers them. Typed as a
 * `Record<IdentifierType, ...>` so the build fails when the API adds a type missing here.
 */
const ID_ORDER: Record<IdentifierType, number> = {
  RFC: 0,
  CURP: 1,
  INE: 2,
  CLABE: 3,
  PASSPORT: 4,
  CPF: 5,
  CNPJ: 6,
  CUIT: 7,
  RUT: 8,
  NIT: 9,
  CEDULA_CO: 10,
  DNI_PE: 11,
}

export const IDENTIFIER_TYPES = (Object.keys(ID_ORDER) as IdentifierType[]).sort((a, b) => ID_ORDER[a] - ID_ORDER[b])

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const COUNTRY = /^[A-Z]{2}$/

// The same bounds as the API's request models, so what the form lets through the API takes.
const shared = {
  countryCode: z.string().trim().regex(COUNTRY, msgKey('countryCode')),
  identifierType: z.enum(IDENTIFIER_TYPES as [IdentifierType, ...IdentifierType[]]),
  identifierValue: z.string().trim().min(1, msgKey('required')).max(64, msgKey('identifierValue')),
}

const person = z.object({
  partyType: z.literal('PERSON'),
  givenNames: z.string().trim().min(1, msgKey('required')).max(200, msgKey('tooLong')),
  firstSurname: z.string().trim().min(1, msgKey('required')).max(100, msgKey('tooLong')),
  secondSurname: z.string().trim().max(100, msgKey('tooLong')),
  birthDate: z.union([z.literal(''), z.string().regex(ISO_DATE, msgKey('dateFormat'))]),
  ...shared,
})

const organization = z.object({
  partyType: z.literal('ORGANIZATION'),
  legalName: z.string().trim().min(1, msgKey('required')).max(300, msgKey('tooLong')),
  tradeName: z.string().trim().max(300, msgKey('tooLong')),
  ...shared,
})

export const partySchema = z.discriminatedUnion('partyType', [person, organization])

export type PartyFormValues = z.input<typeof partySchema>
export type PartyValues = z.output<typeof partySchema>
export type PersonFormValues = z.input<typeof person>
export type OrganizationFormValues = z.input<typeof organization>

const COMMON = { countryCode: 'MX', identifierType: 'RFC', identifierValue: '' } as const

/** A blank form of either kind; the country and the identifier type start as Mexico and RFC. */
export function emptyParty(partyType: 'PERSON'): PersonFormValues
export function emptyParty(partyType: 'ORGANIZATION'): OrganizationFormValues
export function emptyParty(partyType: PartyValues['partyType']): PartyFormValues
export function emptyParty(partyType: PartyValues['partyType']): PartyFormValues {
  return partyType === 'PERSON'
    ? { partyType, givenNames: '', firstSurname: '', secondSurname: '', birthDate: '', ...COMMON }
    : { partyType, legalName: '', tradeName: '', ...COMMON }
}

/** The API's request for a valid form; optional fields left empty are not sent. */
export function toPartyRequest(values: PartyValues): PartyCreateRequest {
  const identifiers = [
    { id_type: values.identifierType, value: values.identifierValue, country_code: values.countryCode },
  ]
  if (values.partyType === 'PERSON') {
    return {
      party_type: 'PERSON',
      country_code: values.countryCode,
      person: {
        given_names: values.givenNames,
        first_surname: values.firstSurname,
        ...(values.secondSurname !== '' && { second_surname: values.secondSurname }),
        ...(values.birthDate !== '' && { birth_date: values.birthDate }),
      },
      identifiers,
    }
  }
  return {
    party_type: 'ORGANIZATION',
    country_code: values.countryCode,
    organization: {
      legal_name: values.legalName,
      ...(values.tradeName !== '' && { trade_name: values.tradeName }),
    },
    identifiers,
  }
}

/** The API's name for each form field, to point a server-side validation error at its input. */
export const FORM_FIELD_OF = {
  given_names: 'givenNames',
  first_surname: 'firstSurname',
  second_surname: 'secondSurname',
  birth_date: 'birthDate',
  legal_name: 'legalName',
  trade_name: 'tradeName',
  country_code: 'countryCode',
} as const
