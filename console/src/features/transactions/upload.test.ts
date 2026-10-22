import { describe, expect, it } from 'vitest'
import { ACCEPTED, ACCEPT_ATTRIBUTE, checkUpload, contentTypeFor, MAX_FILE_MB } from './upload'

const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

function file(name: string, type = '', size = 10): File {
  const made = new File(['x'], name, { type })
  Object.defineProperty(made, 'size', { value: size })
  return made
}

describe('contentTypeFor', () => {
  it('takes the type the browser gives when it is one of the three', () => {
    expect(contentTypeFor(file('movs.csv', 'text/csv'))).toBe('text/csv')
    expect(contentTypeFor(file('movs.jsonl', 'application/x-ndjson'))).toBe('application/x-ndjson')
    expect(contentTypeFor(file('movs.xlsx', XLSX))).toBe(XLSX)
  })

  it('falls back to the suffix when the browser gives none or another one', () => {
    expect(contentTypeFor(file('movs.csv'))).toBe('text/csv')
    expect(contentTypeFor(file('MOVS.CSV', 'application/vnd.ms-excel'))).toBe('text/csv')
    expect(contentTypeFor(file('movs.jsonl', 'application/octet-stream'))).toBe('application/x-ndjson')
    expect(contentTypeFor(file('movs.xlsx'))).toBe(XLSX)
  })

  it('is null for anything else', () => {
    expect(contentTypeFor(file('movs.pdf', 'application/pdf'))).toBeNull()
    expect(contentTypeFor(file('movs.json', 'application/json'))).toBeNull()
    expect(contentTypeFor(file('movs'))).toBeNull()
  })
})

describe('checkUpload', () => {
  it('accepts a supported file up to the cap', () => {
    expect(checkUpload(file('movs.csv', 'text/csv', MAX_FILE_MB * 1024 * 1024))).toBe('ok')
  })

  it('refuses a file over the cap', () => {
    expect(checkUpload(file('movs.csv', 'text/csv', MAX_FILE_MB * 1024 * 1024 + 1))).toBe('tooLarge')
  })

  it('refuses an unsupported file before its size', () => {
    expect(checkUpload(file('movs.pdf', 'application/pdf', MAX_FILE_MB * 1024 * 1024 + 1))).toBe('unsupported')
  })

  it('offers the three suffixes to the file picker', () => {
    expect(Object.keys(ACCEPTED)).toHaveLength(3)
    expect(ACCEPT_ATTRIBUTE).toBe('.csv,.jsonl,.xlsx')
  })
})
