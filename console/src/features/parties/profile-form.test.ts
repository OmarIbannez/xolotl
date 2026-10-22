import { describe, expect, it } from 'vitest'
import {
  declaredFormOf,
  declaredProfileSchema,
  formatMxnMinor,
  formatRatio,
  formatShare,
  minorToInput,
  parseMxn,
  readDeclared,
  readDeviation,
  readMetrics,
  toDeclaredProfileRequest,
} from './profile-form'

/** The issue messages (`validation.*` keys) of a failed parse, by path. */
function issues(result: { success: boolean; error?: { issues: { path: PropertyKey[]; message: string }[] } }) {
  return Object.fromEntries((result.error?.issues ?? []).map((issue) => [issue.path.join('.'), issue.message]))
}

const valid = { inflow: '125000.50', outflow: '80000', countries: 'MX, us', instruments: ['TRANSFER', 'CASH'] }

describe('MXN amounts as minor units, without floats', () => {
  it('reads pesos with up to two decimals as centavos', () => {
    expect(parseMxn('125000.50')).toBe(12500050n)
    expect(parseMxn('125000.5')).toBe(12500050n)
    expect(parseMxn('125,000')).toBe(12500000n)
    expect(parseMxn(' 0 ')).toBe(0n)
    expect(parseMxn('0.07')).toBe(7n)
    expect(parseMxn('1,234.5')).toBe(123450n)
    expect(parseMxn('1,234,567.89')).toBe(123456789n)
    // The 13-digit cap keeps the largest amount's centavos a safe integer for the JSON body.
    expect(parseMxn('9999999999999.99')).toBe(999999999999999n)
  })

  it('refuses a negative amount, a third decimal, letters, an empty input and an absurd size', () => {
    expect(parseMxn('-1')).toBeNull()
    expect(parseMxn('1.005')).toBeNull()
    expect(parseMxn('12a')).toBeNull()
    expect(parseMxn('')).toBeNull()
    expect(parseMxn('10000000000000')).toBeNull()
  })

  it('takes a comma only as a thousands separator, never as a decimal one', () => {
    // A decimal comma read as grouping would declare 100 times the amount.
    expect(parseMxn('125000,50')).toBeNull()
    expect(parseMxn('1,5')).toBeNull()
    expect(parseMxn('1,2,3')).toBeNull()
    expect(parseMxn('1234,567')).toBeNull()
    expect(parseMxn(',125')).toBeNull()
    expect(parseMxn('125,')).toBeNull()
  })

  it('writes centavos back as an input value', () => {
    expect(minorToInput(12500050n)).toBe('125000.50')
    expect(minorToInput(7n)).toBe('0.07')
    expect(minorToInput(0n)).toBe('0.00')
  })

  it('formats centavo strings in the active locale with the currency', () => {
    expect(formatMxnMinor('1900050', 'es-MX')).toBe('19,000.50 MXN')
    expect(formatMxnMinor('1900050', 'en')).toBe('19,000.50 MXN')
    expect(formatMxnMinor('900719925474099312', 'en')).toBe('9,007,199,254,740,993.12 MXN')
    expect(formatMxnMinor(5n, 'es-MX')).toBe('0.05 MXN')
  })

  it('formats a four-decimal share as a percentage and a ratio as a multiple', () => {
    expect(formatShare('0.1234', 'es-MX')).toBe('12.34 %')
    expect(formatShare('1.0000', 'en')).toBe('100.00 %')
    expect(formatShare('0.0000', 'en')).toBe('0.00 %')
    expect(formatRatio('2.5', 'es-MX')).toBe('×2.5')
  })
})

describe('the declared profile form', () => {
  it('converts the amounts to minor units and builds the PATCH body', () => {
    const values = declaredProfileSchema.parse(valid)
    expect(toDeclaredProfileRequest(values)).toEqual({
      declared_profile: {
        monthly_inflow_minor: 12500050,
        monthly_outflow_minor: 8000000,
        currency: 'MXN',
        expected_countries: ['MX', 'US'],
        expected_instruments: ['TRANSFER', 'CASH'],
      },
    })
  })

  it('sends empty lists when no country or instrument is expected', () => {
    const values = declaredProfileSchema.parse({ ...valid, countries: '', instruments: [] })
    expect(toDeclaredProfileRequest(values).declared_profile).toMatchObject({
      expected_countries: [],
      expected_instruments: [],
    })
  })

  it('refuses a negative or malformed amount, a bad country code and a bad instrument code', () => {
    expect(issues(declaredProfileSchema.safeParse({ ...valid, inflow: '-5' }))).toEqual({ inflow: 'amountMxn' })
    expect(issues(declaredProfileSchema.safeParse({ ...valid, outflow: '' }))).toEqual({ outflow: 'amountMxn' })
    expect(issues(declaredProfileSchema.safeParse({ ...valid, countries: 'MEX' }))).toEqual({ countries: 'countryCodes' })
    expect(issues(declaredProfileSchema.safeParse({ ...valid, instruments: ['cash'] }))).toEqual({ instruments: 'instrumentCodes' })
  })

  it('prefills from a stored declaration, and from nothing', () => {
    expect(
      declaredFormOf({
        monthly_inflow_minor: 12500050,
        monthly_outflow_minor: 0,
        currency: 'MXN',
        expected_countries: ['MX'],
        expected_instruments: ['CASH'],
      }),
    ).toEqual({ inflow: '125000.50', outflow: '0.00', countries: 'MX', instruments: ['CASH'] })
    expect(declaredFormOf(null)).toEqual({ inflow: '', outflow: '', countries: '', instruments: [] })
  })
})

describe('reading the free-form JSON of a profile leniently', () => {
  it('reads a declaration, ignoring what is not an amount or a code list', () => {
    expect(readDeclared({ monthly_inflow_minor: 100, monthly_outflow_minor: 'x', expected_countries: ['MX', 3] })).toEqual({
      inflowMinor: 100n,
      outflowMinor: null,
      countries: ['MX'],
      instruments: [],
    })
    expect(readDeclared(null)).toBeNull()
    expect(readDeclared(undefined)).toBeNull()
  })

  it('reads the metrics with amounts as centavo strings', () => {
    expect(
      readMetrics({
        inflow_minor: '1900050',
        outflow_minor: '250000',
        inflow_count: 3,
        outflow_count: 2,
        distinct_counterparties: 4,
        active_days: 5,
        cash_share: '0.1000',
        international_share: '0.0500',
        crypto_share: '0.0000',
        currency: 'MXN',
      }),
    ).toEqual({
      inflowMinor: '1900050',
      outflowMinor: '250000',
      inflowCount: 3,
      outflowCount: 2,
      distinctCounterparties: 4,
      activeDays: 5,
      cashShare: '0.1000',
      internationalShare: '0.0500',
      cryptoShare: '0.0000',
    })
    expect(readMetrics({}).inflowMinor).toBe('0')
    expect(readMetrics({ inflow_minor: 'abc' }).inflowMinor).toBe('0')
  })

  it('reads a ratio per direction and a declared zero as its own reason', () => {
    expect(readDeviation({ monthly_inflow: '2.5', monthly_outflow: null, reasons: { monthly_outflow: 'DECLARED_ZERO' } })).toEqual({
      inflow: { kind: 'ratio', ratio: '2.5' },
      outflow: { kind: 'declaredZero' },
    })
    expect(readDeviation({ monthly_outflow: '0.4' })).toEqual({ inflow: null, outflow: { kind: 'ratio', ratio: '0.4' } })
    expect(readDeviation(null)).toBeNull()
  })
})
