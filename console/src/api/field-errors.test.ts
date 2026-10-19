import { describe, expect, it } from 'vitest'
import { ApiError } from './errors'
import { fieldProblems } from './field-errors'

const FIELDS = ['name', 'scopes', 'env'] as const

const validationFailed = (errors: { loc: (string | number)[]; msg: string; type: string }[]) =>
  new ApiError({ status: 422, code: 'VALIDATION_FAILED', detail: 'request validation failed', errors })

describe('fieldProblems', () => {
  it('points each body error at its form field, with a message key for its kind', () => {
    const error = validationFailed([
      { loc: ['body', 'name'], msg: 'String should have at least 1 character', type: 'string_too_short' },
      { loc: ['body', 'scopes'], msg: 'List should have at least 1 item', type: 'too_short' },
      { loc: ['body', 'env'], msg: "Input should be 'SANDBOX' or 'PROD'", type: 'enum' },
    ])

    expect(fieldProblems(error, FIELDS)).toEqual([
      { field: 'name', message: 'required' },
      { field: 'scopes', message: 'required' },
      { field: 'env', message: 'invalid' },
    ])
  })

  it('maps a missing field to required and an over-long one to tooLong', () => {
    const error = validationFailed([
      { loc: ['body', 'name'], msg: 'Field required', type: 'missing' },
      { loc: ['body', 'scopes'], msg: 'too long', type: 'too_long' },
    ])

    expect(fieldProblems(error, FIELDS)).toEqual([
      { field: 'name', message: 'required' },
      { field: 'scopes', message: 'tooLong' },
    ])
    expect(
      fieldProblems(
        validationFailed([{ loc: ['body', 'name'], msg: 'String should have at most 120 characters', type: 'string_too_long' }]),
        FIELDS,
      ),
    ).toEqual([{ field: 'name', message: 'tooLong' }])
  })

  it('reads an error about an item of a list as an error of the list', () => {
    const error = validationFailed([{ loc: ['body', 'scopes', 2], msg: 'bad item', type: 'string_pattern_mismatch' }])

    expect(fieldProblems(error, FIELDS)).toEqual([{ field: 'scopes', message: 'invalid' }])
  })

  it('keeps the first problem of a field', () => {
    const error = validationFailed([
      { loc: ['body', 'name'], msg: 'a', type: 'string_too_short' },
      { loc: ['body', 'name'], msg: 'b', type: 'string_too_long' },
    ])

    expect(fieldProblems(error, FIELDS)).toEqual([{ field: 'name', message: 'required' }])
  })

  it('leaves out errors about things the form does not have, or that are not about the body', () => {
    const error = validationFailed([
      { loc: ['body', 'unknown_field'], msg: 'x', type: 'missing' },
      { loc: ['query', 'name'], msg: 'x', type: 'missing' },
      { loc: [], msg: 'x', type: 'missing' },
    ])

    expect(fieldProblems(error, FIELDS)).toEqual([])
  })

  it('finds nothing in a validation failure that carries no field errors', () => {
    // The service layer raises these (a scope that breaks the grammar, a malformed email) as a bare 422.
    expect(fieldProblems(validationFailed([]), FIELDS)).toEqual([])
  })

  it('finds nothing in any other kind of failure', () => {
    expect(fieldProblems(new ApiError({ status: 409, code: 'CONFLICT', detail: 'exists' }), FIELDS)).toEqual([])
    expect(fieldProblems(new Error('boom'), FIELDS)).toEqual([])
    expect(fieldProblems(undefined, FIELDS)).toEqual([])
  })
})
