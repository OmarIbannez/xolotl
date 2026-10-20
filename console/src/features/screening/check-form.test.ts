import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/errors'
import esMX from '@/i18n/es-MX.json'
import { checkFieldProblems, checkSchema, emptyCheck, toCheckRequest, type CheckFormValues } from './check-form'

const PERSON: CheckFormValues = {
  ...emptyCheck(),
  names: [{ value: ' Juan Pérez López ' }, { value: 'Juanito Pérez' }],
  givenNames: 'Juan',
  firstSurname: 'Pérez',
  secondSurname: '',
  birthDate: '1976-05-10',
  gender: 'MALE',
  nationalities: 'mx, ve',
  countries: 'MX',
  identifiers: [{ idType: 'RFC', value: ' PELJ760510AB8 ' }],
}

/** The `validation.*` text each failing input would show, by its path. */
function problems(values: CheckFormValues): Record<string, string> {
  const result = checkSchema.safeParse(values)
  if (result.success) return {}
  return Object.fromEntries(
    result.error.issues.map((issue) => [
      issue.path.join('.'),
      esMX.validation[issue.message as keyof typeof esMX.validation] ?? issue.message,
    ]),
  )
}

describe('checkSchema and toCheckRequest', () => {
  it('maps a person to an inline subject, trimmed, with the country lists upper-cased and no empty input', () => {
    expect(toCheckRequest(checkSchema.parse(PERSON))).toEqual({
      subject: {
        type: 'PERSON',
        names: ['Juan Pérez López', 'Juanito Pérez'],
        given_names: 'Juan',
        first_surname: 'Pérez',
        birth_date: '1976-05-10',
        gender: 'MALE',
        nationalities: ['MX', 'VE'],
        countries: ['MX'],
        identifiers: [{ id_type: 'RFC', value: 'PELJ760510AB8' }],
      },
    })
  })

  it('sends only the names (and countries) of an organisation, never the person-only inputs', () => {
    const values: CheckFormValues = { ...PERSON, entityType: 'ORGANIZATION', names: [{ value: 'Acme SA de CV' }], identifiers: [] }

    expect(toCheckRequest(checkSchema.parse(values))).toEqual({
      subject: { type: 'ORGANIZATION', names: ['Acme SA de CV'], countries: ['MX'] },
    })
  })

  it('sends the chosen lists, and no lists at all when none is chosen', () => {
    const minimal: CheckFormValues = { ...emptyCheck(), names: [{ value: 'Ana' }] }

    expect(toCheckRequest(checkSchema.parse(minimal))).toEqual({ subject: { type: 'PERSON', names: ['Ana'] } })
    expect(toCheckRequest(checkSchema.parse({ ...minimal, lists: ['OFAC_SDN'] })).lists).toEqual(['OFAC_SDN'])
  })

  it('requires every name and identifier value, and checks the dates, the country codes and the lengths', () => {
    expect(
      problems({
        ...PERSON,
        names: [{ value: ' ' }, { value: 'x'.repeat(301) }],
        birthDate: '10/05/1976',
        nationalities: 'MEX',
        identifiers: [{ idType: 'CURP', value: '' }],
      }),
    ).toEqual({
      'names.0.value': esMX.validation.required,
      'names.1.value': esMX.validation.tooLong,
      birthDate: esMX.validation.dateFormat,
      nationalities: esMX.validation.countryCodes,
      'identifiers.0.value': esMX.validation.required,
    })
  })

  it('rejects a birth date that is not a calendar date', () => {
    expect(problems({ ...PERSON, birthDate: '2024-02-30' })).toEqual({ birthDate: esMX.validation.dateFormat })
    expect(problems({ ...PERSON, birthDate: '2024-02-29' })).toEqual({})
  })

  it('takes at most three names and ten identifiers', () => {
    const four = Array.from({ length: 4 }, () => ({ value: 'Ana' }))
    const eleven = Array.from({ length: 11 }, () => ({ idType: 'RFC' as const, value: 'X' }))

    expect(Object.keys(problems({ ...PERSON, names: four }))).toEqual(['names'])
    expect(Object.keys(problems({ ...PERSON, identifiers: eleven }))).toEqual(['identifiers'])
  })
})

describe('checkFieldProblems', () => {
  const refusal = (loc: (string | number)[], type = 'value_error') =>
    new ApiError({ status: 422, code: 'VALIDATION_FAILED', detail: 'x', errors: [{ loc, msg: 'x', type }] })

  it('points a problem of the subject at its input', () => {
    expect(checkFieldProblems(refusal(['body', 'subject', 'birth_date'], 'date_from_datetime_parsing'))).toEqual([
      { field: 'birthDate', message: 'invalid' },
    ])
    expect(checkFieldProblems(refusal(['body', 'subject', 'names', 1], 'string_too_long'))).toEqual([
      { field: 'names.1.value', message: 'tooLong' },
    ])
    expect(checkFieldProblems(refusal(['body', 'subject', 'identifiers', 2, 'value'], 'missing'))).toEqual([
      { field: 'identifiers.2.value', message: 'required' },
    ])
    expect(checkFieldProblems(refusal(['body', 'subject', 'identifiers', 0, 'id_type']))).toEqual([
      { field: 'identifiers.0.idType', message: 'invalid' },
    ])
  })

  it('finds nothing for a problem outside the subject or another refusal', () => {
    expect(checkFieldProblems(refusal(['body', 'lists']))).toEqual([])
    expect(checkFieldProblems(new ApiError({ status: 422, code: 'IDENTIFIER_INVALID', detail: 'RFC: CHECKSUM' }))).toEqual([])
    expect(checkFieldProblems(new Error('boom'))).toEqual([])
  })
})
