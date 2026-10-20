import { z } from 'zod'
import { ApiError, isApiError } from '@/api/errors'
import { fieldProblems, type FieldProblem } from '@/api/field-errors'
import type { AdHocSubjectInput, CheckRequest, IdentifierType } from '@/api/types'
import { IDENTIFIER_TYPES } from '@/features/parties/form'
import { msgKey } from '@/i18n/validation'

// The ad hoc check form (Consulta ad hoc). Its bounds are the API's `AdHocSubjectInput`, so what
// the form lets through the API takes. Everything typed here is personal data: it lives in the
// form's state only, and is sent once, in the body of the check.

export const MAX_NAMES = 3
export const MAX_IDENTIFIERS = 10
/** The API's bound on `lists`. */
const MAX_LISTS = 20
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const COUNTRY = /^[A-Z]{2}$/

export const ENTITY_TYPES = ['PERSON', 'ORGANIZATION'] as const
export const GENDERS = ['MALE', 'FEMALE'] as const

/** `"mx, ve"` -> `['MX', 'VE']`: commas or spaces between two-letter codes, any case. */
function countryList(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((code) => code.trim().toUpperCase())
    .filter((code) => code !== '')
}

const countries = z
  .string()
  .transform(countryList)
  .refine((codes) => codes.every((code) => COUNTRY.test(code)), msgKey('countryCodes'))

const personText = z.string().trim().max(300, msgKey('tooLong'))

export const checkSchema = z.object({
  entityType: z.enum(ENTITY_TYPES),
  names: z
    .array(z.object({ value: z.string().trim().min(1, msgKey('required')).max(300, msgKey('tooLong')) }))
    .min(1)
    .max(MAX_NAMES),
  givenNames: personText,
  firstSurname: personText,
  secondSurname: personText,
  birthDate: z.union([z.literal(''), z.string().regex(ISO_DATE, msgKey('dateFormat'))]),
  gender: z.enum(['', ...GENDERS]),
  nationalities: countries,
  countries,
  identifiers: z
    .array(
      z.object({
        idType: z.enum(IDENTIFIER_TYPES as [IdentifierType, ...IdentifierType[]]),
        value: z.string().trim().min(1, msgKey('required')).max(64, msgKey('identifierValue')),
      }),
    )
    .max(MAX_IDENTIFIERS),
  lists: z.array(z.string()).max(MAX_LISTS),
})

export type CheckFormValues = z.input<typeof checkSchema>
export type CheckValues = z.output<typeof checkSchema>

/** A blank form: a person with one empty name, no identifiers and every list. */
export function emptyCheck(): CheckFormValues {
  return {
    entityType: 'PERSON',
    names: [{ value: '' }],
    givenNames: '',
    firstSurname: '',
    secondSurname: '',
    birthDate: '',
    gender: '',
    nationalities: '',
    countries: '',
    identifiers: [],
    lists: [],
  }
}

/**
 * The API's request for a valid form. Optional inputs left empty are not sent; the person-only
 * inputs are not sent for an organisation; no chosen list means every list of the tenant.
 */
export function toCheckRequest(values: CheckValues): CheckRequest {
  const person = values.entityType === 'PERSON'
  const subject: AdHocSubjectInput = {
    type: values.entityType,
    names: values.names.map((name) => name.value),
    ...(person && values.givenNames !== '' && { given_names: values.givenNames }),
    ...(person && values.firstSurname !== '' && { first_surname: values.firstSurname }),
    ...(person && values.secondSurname !== '' && { second_surname: values.secondSurname }),
    ...(person && values.birthDate !== '' && { birth_date: values.birthDate }),
    ...(person && values.gender !== '' && { gender: values.gender }),
    ...(person && values.nationalities.length > 0 && { nationalities: values.nationalities }),
    ...(values.countries.length > 0 && { countries: values.countries }),
    ...(values.identifiers.length > 0 && {
      identifiers: values.identifiers.map((identifier) => ({ id_type: identifier.idType, value: identifier.value })),
    }),
  }
  return { subject, ...(values.lists.length > 0 && { lists: values.lists }) }
}

/** The API's name for each single input of the subject. */
const FORM_FIELD_OF = {
  given_names: 'givenNames',
  first_surname: 'firstSurname',
  second_surname: 'secondSurname',
  birth_date: 'birthDate',
  gender: 'gender',
  nationalities: 'nationalities',
  countries: 'countries',
} as const

export type CheckFieldPath =
  | (typeof FORM_FIELD_OF)[keyof typeof FORM_FIELD_OF]
  | `names.${number}.value`
  | `identifiers.${number}.idType`
  | `identifiers.${number}.value`

/** Where a problem the API reports at `['body', 'subject', ...rest]` belongs on the form. */
function formPath(rest: readonly (string | number)[]): CheckFieldPath | undefined {
  const [field, index, leaf] = rest
  if (typeof field !== 'string') return undefined
  const at = typeof index === 'number' ? index : 0
  if (field === 'names') return `names.${at}.value`
  if (field === 'identifiers') return leaf === 'id_type' ? `identifiers.${at}.idType` : `identifiers.${at}.value`
  return field in FORM_FIELD_OF ? FORM_FIELD_OF[field as keyof typeof FORM_FIELD_OF] : undefined
}

/**
 * The per-input problems of a 422 `VALIDATION_FAILED` answer to a check, at the inputs of this
 * form (`['body', 'subject', 'birth_date']` -> `birthDate`, `['body', 'subject', 'names', 1]` ->
 * `names.1.value`). Anything else finds nothing, and the form shows the failure as a whole.
 */
export function checkFieldProblems(error: unknown): FieldProblem<CheckFieldPath>[] {
  if (!isApiError(error)) return []
  const paths: CheckFieldPath[] = []
  const errors = error.errors.map((problem) => {
    const [where, subject, ...rest] = problem.loc
    const path = where === 'body' && subject === 'subject' ? formPath(rest) : undefined
    if (path === undefined) return problem
    paths.push(path)
    return { ...problem, loc: ['body', path] }
  })
  const located = new ApiError({
    status: error.status,
    code: error.code,
    detail: error.detail,
    requestId: error.requestId,
    errors,
  })
  return fieldProblems(located, paths)
}
