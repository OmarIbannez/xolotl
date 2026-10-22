import type { ParamValue, ParamView, SegmentFilter } from '@/api/types'

// What the version editor reads and sends. A decimal parameter never passes through a float: the
// API spells it as text and refuses a JSON float, so the editor keeps the digits and sends a string.

/**
 * A decimal with an optional fraction after a point. A comma is accepted only as well-formed
 * thousands grouping ("5,000.50": a first group of one to three digits not starting with 0, then
 * groups of three), never as a decimal separator: "0,70" and "0,700" are refused rather than read
 * as 70 or 700 (the rule of the declared profile's amounts).
 */
const DECIMAL = /^([1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.(\d+))?$/
const INTEGER = /^\d+$/

/** "5,000.50" or "0.70" as the API spells it ("5000.50", "0.70"); null for anything else. */
export function parseDecimal(text: string): string | null {
  const match = DECIMAL.exec(text.trim())
  if (match === null) return null
  const [, whole = '0', fraction] = match
  return fraction === undefined ? whole.replaceAll(',', '') : `${whole.replaceAll(',', '')}.${fraction}`
}

/** What the form holds for a parameter: text for every type but booleans. */
export type ParamInput = string | boolean

/** The form's starting value of a parameter. */
export function inputOf(param: ParamView): ParamInput {
  return param.type === 'BOOL' ? param.value === 'true' : param.value
}

/**
 * The value to send for a parameter, typed as the API wants it (a decimal as a string, an integer
 * as a number); null when the text is not a value of that type.
 */
export function valueOf(param: ParamView, input: ParamInput): ParamValue | null {
  if (typeof input === 'boolean') return param.type === 'BOOL' ? input : null
  const text = input.trim()
  switch (param.type) {
    case 'DECIMAL':
      return parseDecimal(text)
    case 'INT':
      return INTEGER.test(text) && Number.isSafeInteger(Number(text)) ? Number(text) : null
    case 'STR':
      return text === '' ? null : text
    case 'BOOL':
      return null
  }
}

/** Codes separated by commas or spaces, upper-cased, without repeats: "n1, N2" is `['N1', 'N2']`. */
export function splitCodes(text: string): string[] {
  const codes = text
    .split(/[\s,]+/)
    .map((code) => code.trim().toUpperCase())
    .filter((code) => code !== '')
  return [...new Set(codes)]
}

export type SegmentField = keyof Required<SegmentFilter>
export const SEGMENT_FIELDS: readonly SegmentField[] = ['tier', 'risk_rating', 'monitoring_profile']

/** The segment as the form's three code lists. */
export function segmentInputOf(segment: SegmentFilter | null): Record<SegmentField, string> {
  return {
    tier: (segment?.tier ?? []).join(', '),
    risk_rating: (segment?.risk_rating ?? []).join(', '),
    monitoring_profile: (segment?.monitoring_profile ?? []).join(', '),
  }
}

/**
 * The segment to send, always with its three lists: empty lists mean every customer. Never null,
 * which the API reads as "keep the stored segment", so a cleared segment would silently stay.
 */
export function segmentOf(input: Record<SegmentField, string>): SegmentFilter {
  return {
    tier: splitCodes(input.tier),
    risk_rating: splitCodes(input.risk_rating),
    monitoring_profile: splitCodes(input.monitoring_profile),
  }
}

function dayString(date: Date): string {
  return date.toISOString().slice(0, 10)
}

/**
 * The six months that end on `today` (a Mexico City day, `YYYY-MM-DD`), both ends included: from
 * the day after the same day six months earlier (clamped to the end of a shorter month).
 */
export function lastSixMonths(today: string): { range_start: string; range_end: string } {
  const [year = 0, month = 1, day = 1] = today.split('-').map(Number)
  const lastDay = new Date(Date.UTC(year, month - 6, 0)).getUTCDate()
  const start = new Date(Date.UTC(year, month - 7, Math.min(day, lastDay) + 1))
  return { range_start: dayString(start), range_end: today }
}
