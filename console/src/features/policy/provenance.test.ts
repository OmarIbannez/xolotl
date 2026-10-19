import { describe, expect, it } from 'vitest'
import type { ConversionResponse } from '@/api/types'
import type { Locale } from '@/i18n'
import {
  formatDate,
  formatDateTime,
  formatNumber,
  formatRegion,
  formatShortDate,
  formatTime,
} from '@/lib/format'
import { describeConversion, formatQuantity, provenanceOf } from './provenance'

/** What `useFormat()` returns for `locale`, without rendering a component. */
function formatFor(locale: Locale) {
  return {
    locale,
    date: (value: string | Date) => formatDate(value, locale),
    dateTime: (value: string | Date) => formatDateTime(value, locale),
    shortDate: (value: string | Date) => formatShortDate(value, locale),
    time: (value: string | Date) => formatTime(value, locale),
    number: (value: number) => formatNumber(value, locale),
    region: (code: string) => formatRegion(code, locale),
  }
}

const CONVERSION: ConversionResponse = {
  amount: '6390.00',
  currency: 'MXN',
  unit: 'UDI',
  rate: '8.52000000',
  unit_value_id: 7,
  rule: 'LAST_DAY_PREV_MONTH',
  resolved_date: '2025-12-31',
}

describe('formatQuantity', () => {
  it('writes the amount as the API sent it, then the unit', () => {
    expect(formatQuantity({ amount: '750', unit: 'UDI' })).toBe('750 UDI')
    expect(formatQuantity({ amount: '1000', unit: 'UDI' })).toBe('1000 UDI')
  })
})

describe('describeConversion', () => {
  it('is a dash when nothing was converted', () => {
    expect(describeConversion(null, formatFor('es-MX'))).toBe('—')
    expect(describeConversion(undefined, formatFor('es-MX'))).toBe('—')
  })

  it('gives the amount with two decimals, the currency, the unit and the day of its value', () => {
    const text = describeConversion(CONVERSION, formatFor('es-MX'))
    expect(text).toContain('6,390.00')
    expect(text).toContain('2025-12-31')
    expect(text).toBe('6,390.00 MXN · UDI 2025-12-31')
    expect(describeConversion(CONVERSION, formatFor('en'))).toBe('6,390.00 MXN · UDI 2025-12-31')
  })
})

describe('provenanceOf', () => {
  it('maps the confidence, the legal basis and the verification date', () => {
    expect(
      provenanceOf({ confidence: 'UNVERIFIED', legal_basis: 'DCG art. 58', verified_as_of: '2026-10-18' }),
    ).toEqual({ confidence: 'UNVERIFIED', legalBasis: 'DCG art. 58', verifiedAsOf: '2026-10-18' })
  })
})
