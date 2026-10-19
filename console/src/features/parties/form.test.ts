import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { emptyParty, IDENTIFIER_TYPES, partySchema, toPartyRequest, type PartyFormValues } from './form'

const PERSON: PartyFormValues = {
  partyType: 'PERSON',
  givenNames: ' Ana María ',
  firstSurname: 'Pérez',
  secondSurname: '',
  birthDate: '1976-05-10',
  countryCode: 'MX',
  identifierType: 'RFC',
  identifierValue: ' CACX7605101P8 ',
}

/** The `validation.*` text each failing field would show. */
function problems(values: PartyFormValues): Record<string, string> {
  const result = partySchema.safeParse(values)
  if (result.success) return {}
  return Object.fromEntries(
    result.error.issues.map((issue) => [
      String(issue.path[0]),
      esMX.validation[issue.message as keyof typeof esMX.validation],
    ]),
  )
}

describe('partySchema and toPartyRequest', () => {
  it('maps a person with an empty second surname to a request without one, trimmed, with birth_date and the identifier', () => {
    const request = toPartyRequest(partySchema.parse(PERSON))

    expect(request).toEqual({
      party_type: 'PERSON',
      country_code: 'MX',
      person: { given_names: 'Ana María', first_surname: 'Pérez', birth_date: '1976-05-10' },
      identifiers: [{ id_type: 'RFC', value: 'CACX7605101P8', country_code: 'MX' }],
    })
    expect(request.person).not.toHaveProperty('second_surname')
  })

  it('sends the second surname when there is one, and no birth date when it is empty', () => {
    const request = toPartyRequest(partySchema.parse({ ...PERSON, secondSurname: 'Gómez', birthDate: '' }))

    expect(request.person).toEqual({ given_names: 'Ana María', first_surname: 'Pérez', second_surname: 'Gómez' })
  })

  it('maps an organisation: legalName to organization.legal_name, no trade name when empty', () => {
    const values: PartyFormValues = {
      ...emptyParty('ORGANIZATION'),
      legalName: 'Acme SA de CV',
      identifierValue: 'AAA010101AAA',
    }

    expect(toPartyRequest(partySchema.parse(values))).toEqual({
      party_type: 'ORGANIZATION',
      country_code: 'MX',
      organization: { legal_name: 'Acme SA de CV' },
      identifiers: [{ id_type: 'RFC', value: 'AAA010101AAA', country_code: 'MX' }],
    })
  })

  it('refuses a birth date that is not YYYY-MM-DD', () => {
    expect(problems({ ...PERSON, birthDate: '1976-5-1' })).toEqual({ birthDate: esMX.validation.dateFormat })
  })

  it('requires the names and the identifier, and checks the country code and the lengths', () => {
    expect(problems({ ...PERSON, givenNames: ' ', firstSurname: '', identifierValue: '  ' })).toEqual({
      givenNames: esMX.validation.required,
      firstSurname: esMX.validation.required,
      identifierValue: esMX.validation.required,
    })
    expect(problems({ ...PERSON, countryCode: 'mex', identifierValue: 'x'.repeat(65) })).toEqual({
      countryCode: esMX.validation.countryCode,
      identifierValue: esMX.validation.identifierValue,
    })
    expect(problems({ ...emptyParty('ORGANIZATION'), legalName: 'x'.repeat(301), identifierValue: 'X' })).toEqual({
      legalName: esMX.validation.tooLong,
    })
  })

  it('starts blank, in Mexico, on RFC, and offers every identifier type once', () => {
    expect(emptyParty('PERSON')).toMatchObject({ countryCode: 'MX', identifierType: 'RFC', identifierValue: '' })
    expect([...IDENTIFIER_TYPES].sort()).toEqual(Object.keys(esMX.parties.identifierType).sort())
  })
})
