import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/errors'
import { rowErrorsOf } from './row-errors'

const invalid = (errors: { loc: (string | number)[]; msg: string; type: string }[]) =>
  new ApiError({ status: 422, code: 'PRIVATE_LIST_INVALID', detail: 'bad rows', errors })

describe('rowErrorsOf', () => {
  it('reads a row error located as ["row", n, column]', () => {
    expect(rowErrorsOf(invalid([{ loc: ['row', 12, 'rfc'], msg: 'invalid RFC', type: 'INVALID_RFC' }]))).toEqual([
      { row: 12, column: 'rfc', code: 'INVALID_RFC' },
    ])
  })

  it('gives a stray value its column number as text', () => {
    expect(rowErrorsOf(invalid([{ loc: ['row', 3, 17], msg: 'x', type: 'UNKNOWN_COLUMN' }]))).toEqual([
      { row: 3, column: '17', code: 'UNKNOWN_COLUMN' },
    ])
  })

  it('reads a whole-file error located as ["file"] with no row or column', () => {
    expect(rowErrorsOf(invalid([{ loc: ['file'], msg: 'no data rows', type: 'EMPTY_FILE' }]))).toEqual([
      { row: null, column: null, code: 'EMPTY_FILE' },
    ])
  })

  it('keeps the code of an error whose loc it cannot read, without a row or column', () => {
    expect(
      rowErrorsOf(
        invalid([
          { loc: [], msg: 'x', type: 'REQUIRED' },
          { loc: ['row', 'twelve', 'rfc'], msg: 'x', type: 'INVALID_RFC' },
          { loc: ['body', 'rfc'], msg: 'x', type: 'INVALID_CURP' },
          { loc: ['row', 4.5, 'curp'], msg: 'x', type: 'INVALID_CURP' },
        ]),
      ),
    ).toEqual([
      { row: null, column: null, code: 'REQUIRED' },
      { row: null, column: null, code: 'INVALID_RFC' },
      { row: null, column: null, code: 'INVALID_CURP' },
      { row: null, column: null, code: 'INVALID_CURP' },
    ])
  })

  it('reads ["row", n] as a row with no column', () => {
    expect(rowErrorsOf(invalid([{ loc: ['row', 7], msg: 'x', type: 'DUPLICATE_KEY' }]))).toEqual([
      { row: 7, column: null, code: 'DUPLICATE_KEY' },
    ])
  })

  it('reads a problem body as well as an ApiError, and nothing from a problem without errors', () => {
    expect(rowErrorsOf({ errors: [{ loc: ['row', 2, 'tipo'], msg: 'x', type: 'INVALID_TYPE' }] })).toEqual([
      { row: 2, column: 'tipo', code: 'INVALID_TYPE' },
    ])
    expect(rowErrorsOf({})).toEqual([])
  })
})
