import { z } from 'zod'
import type { Instrument, RelationshipPatchRequest } from '@/api/types'
import type { Locale } from '@/i18n'
import { msgKey } from '@/i18n/validation'

// The declared monthly profile of a customer relationship and the behaviour profile the API
// computes against it. Amounts never pass through a float: the API sends whole MXN centavos as
// strings, the form reads pesos into `bigint` centavos, and formatting works on the digits.

/** The instruments the monitoring module knows, offered as the expected ones. */
export const INSTRUMENTS: readonly Instrument[] = ['TRANSFER', 'CASH', 'CARD', 'CHEQUE', 'CRYPTO', 'FX', 'FEE']

const CENTS_PER_PESO = 100n
/** Pesos with up to two decimals; 13 integer digits keep the centavos a safe JSON integer. */
const PESOS = /^(\d{1,13})(?:\.(\d{1,2}))?$/
const COUNTRY = /^[A-Z]{2}$/
/** The API's instrument code pattern (`DeclaredProfile.expected_instruments`). */
const INSTRUMENT = /^[A-Z_]{2,16}$/
const DIGITS = /^\d+$/
const SHARE = /^(\d+)\.(\d{4})$/

/** "125,000.50" or "125000.5" pesos as centavos; null for anything else (a sign, a third decimal). */
export function parseMxn(text: string): bigint | null {
  const match = PESOS.exec(text.trim().replaceAll(',', ''))
  if (match === null) return null
  const [, whole = '0', fraction = ''] = match
  return BigInt(whole) * CENTS_PER_PESO + BigInt(fraction.padEnd(2, '0'))
}

/** Centavos as the form's input value: "125000.50". */
export function minorToInput(minor: bigint): string {
  return `${minor / CENTS_PER_PESO}.${(minor % CENTS_PER_PESO).toString().padStart(2, '0')}`
}

const separators = new Map<Locale, string>()

/** The locale's decimal separator. */
function decimalSeparator(locale: Locale): string {
  const hit = separators.get(locale)
  if (hit !== undefined) return hit
  const found = new Intl.NumberFormat(locale).formatToParts(1.5).find((part) => part.type === 'decimal')?.value ?? '.'
  separators.set(locale, found)
  return found
}

/** A whole number and a fixed fraction of digits, grouped and separated as the locale does. */
function decimalText(whole: bigint, fraction: string, locale: Locale): string {
  return `${new Intl.NumberFormat(locale).format(whole)}${decimalSeparator(locale)}${fraction}`
}

/** "19,000.50 MXN": centavos (a string of digits from the API, or a bigint) in the active locale. */
export function formatMxnMinor(minor: string | bigint, locale: Locale): string {
  const value = typeof minor === 'bigint' ? minor : DIGITS.test(minor) ? BigInt(minor) : 0n
  const cents = (value % CENTS_PER_PESO).toString().padStart(2, '0')
  return `${decimalText(value / CENTS_PER_PESO, cents, locale)} MXN`
}

/** "12.34 %": a share with four decimals ("0.1234") as a percentage with two. */
export function formatShare(share: string, locale: Locale): string {
  const match = SHARE.exec(share)
  if (match === null) return '—'
  const [, whole = '0', fraction = '0000'] = match
  const basisPoints = BigInt(whole) * 10000n + BigInt(fraction)
  return `${decimalText(basisPoints / 100n, (basisPoints % 100n).toString().padStart(2, '0'), locale)} %`
}

/** "×2.5": an actual-to-declared ratio, already rounded down by the API to one place. */
export function formatRatio(ratio: string, locale: Locale): string {
  const [whole = '0', fraction] = ratio.split('.', 2)
  if (!DIGITS.test(whole) || (fraction !== undefined && !DIGITS.test(fraction))) return '—'
  return `×${fraction === undefined ? new Intl.NumberFormat(locale).format(BigInt(whole)) : decimalText(BigInt(whole), fraction, locale)}`
}

function splitCodes(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((code) => code.trim().toUpperCase())
    .filter((code) => code !== '')
}

const amount = z.string().refine((value) => parseMxn(value) !== null, msgKey('amountMxn'))

export const declaredProfileSchema = z.object({
  inflow: amount,
  outflow: amount,
  /** Two-letter codes separated by commas or spaces, for example `MX, US`. */
  countries: z.string().refine((value) => splitCodes(value).every((code) => COUNTRY.test(code)), msgKey('countryCodes')),
  instruments: z.array(z.string()).refine((codes) => codes.every((code) => INSTRUMENT.test(code)), msgKey('instrumentCodes')),
})
export type DeclaredProfileFormValues = z.output<typeof declaredProfileSchema>

/** The PATCH body for the form's values (the schema has already checked them). */
export function toDeclaredProfileRequest(values: DeclaredProfileFormValues): RelationshipPatchRequest {
  return {
    declared_profile: {
      monthly_inflow_minor: Number(parseMxn(values.inflow) ?? 0n),
      monthly_outflow_minor: Number(parseMxn(values.outflow) ?? 0n),
      currency: 'MXN',
      expected_countries: splitCodes(values.countries),
      expected_instruments: values.instruments,
    },
  }
}

/** A declaration as stored, read leniently: what is not an amount or a code list declares nothing. */
export interface Declared {
  inflowMinor: bigint | null
  outflowMinor: bigint | null
  countries: string[]
  instruments: string[]
}

type Json = Readonly<Record<string, unknown>>

function minorOf(value: unknown): bigint | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? BigInt(value) : null
}

function codesOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((code): code is string => typeof code === 'string') : []
}

export function readDeclared(json: Json | null | undefined): Declared | null {
  if (json === null || json === undefined) return null
  return {
    inflowMinor: minorOf(json.monthly_inflow_minor),
    outflowMinor: minorOf(json.monthly_outflow_minor),
    countries: codesOf(json.expected_countries),
    instruments: codesOf(json.expected_instruments),
  }
}

/** The form's starting values: the stored declaration, or an empty form. */
export function declaredFormOf(json: Json | null | undefined): DeclaredProfileFormValues {
  const declared = readDeclared(json)
  const input = (minor: bigint | null | undefined) => (minor === null || minor === undefined ? '' : minorToInput(minor))
  return {
    inflow: input(declared?.inflowMinor),
    outflow: input(declared?.outflowMinor),
    countries: (declared?.countries ?? []).join(', '),
    instruments: declared?.instruments ?? [],
  }
}

/** One month's metrics: amounts are centavo strings, shares strings with four decimals. */
export interface ProfileMetrics {
  inflowMinor: string
  outflowMinor: string
  inflowCount: number
  outflowCount: number
  distinctCounterparties: number
  activeDays: number
  cashShare: string
  internationalShare: string
  cryptoShare: string
}

const digitsOf = (value: unknown) => (typeof value === 'string' && DIGITS.test(value) ? value : '0')
const countOf = (value: unknown) => (typeof value === 'number' && Number.isInteger(value) ? value : 0)
const shareOf = (value: unknown) => (typeof value === 'string' && SHARE.test(value) ? value : '0.0000')

export function readMetrics(json: Json): ProfileMetrics {
  return {
    inflowMinor: digitsOf(json.inflow_minor),
    outflowMinor: digitsOf(json.outflow_minor),
    inflowCount: countOf(json.inflow_count),
    outflowCount: countOf(json.outflow_count),
    distinctCounterparties: countOf(json.distinct_counterparties),
    activeDays: countOf(json.active_days),
    cashShare: shareOf(json.cash_share),
    internationalShare: shareOf(json.international_share),
    cryptoShare: shareOf(json.crypto_share),
  }
}

/**
 * How a direction compares with its declaration: the actual-to-declared ratio, or a declared zero
 * against which the month moved money (no finite ratio; the API counts it as a deviation).
 */
export type DirectionDeviation = { kind: 'ratio'; ratio: string } | { kind: 'declaredZero' }
export interface Deviation {
  inflow: DirectionDeviation | null
  outflow: DirectionDeviation | null
}

function directionOf(json: Json, name: 'monthly_inflow' | 'monthly_outflow'): DirectionDeviation | null {
  const reasons = json.reasons
  if (typeof reasons === 'object' && reasons !== null && (reasons as Json)[name] === 'DECLARED_ZERO') {
    return { kind: 'declaredZero' }
  }
  const ratio = json[name]
  return typeof ratio === 'string' ? { kind: 'ratio', ratio } : null
}

/** Null when the month had no declaration to compare with. */
export function readDeviation(json: Json | null | undefined): Deviation | null {
  if (json === null || json === undefined) return null
  return { inflow: directionOf(json, 'monthly_inflow'), outflow: directionOf(json, 'monthly_outflow') }
}
