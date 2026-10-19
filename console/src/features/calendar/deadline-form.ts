import { z } from 'zod'
import type { DeadlineKind, DeadlineRule, HolidayScope } from '@/api/types'
import { msgKey, type ValidationKey } from '@/i18n/validation'

/**
 * Every kind the API's deadline DSL knows (`platform/calendar/deadlines.py`). Typed as a
 * `Record<DeadlineKind, ...>` so the build fails when the API adds a kind missing here.
 */
const KIND_FIELDS: Record<DeadlineKind, readonly DeadlineField[]> = {
  BUSINESS_DAYS_AFTER: ['n', 'time'],
  NTH_BUSINESS_DAY_OF: ['monthOffset', 'n', 'time'],
  DAY_OF_NEXT_MONTH: ['day', 'roll', 'time'],
  CALENDAR_DAYS_AFTER: ['n', 'time'],
  HOURS_AFTER: ['hours'],
  DATE_RANGE: ['start', 'end', 'yearOffset', 'time'],
  FIXED: ['at'],
}

export const DEADLINE_KINDS = Object.keys(KIND_FIELDS) as DeadlineKind[]
export const SCOPES: readonly HolidayScope[] = ['NATIONAL', 'CNBV', 'SAT', 'BANXICO']
export const ROLLS = ['NEXT_BUSINESS_DAY', 'PREV_BUSINESS_DAY', 'NONE'] as const
export type Roll = (typeof ROLLS)[number]

/** Every rule the console builds is read on the Mexico City clock, the API's default. */
export const DEADLINE_TZ = 'America/Mexico_City'

export type DeadlineField =
  | 'n'
  | 'monthOffset'
  | 'day'
  | 'hours'
  | 'roll'
  | 'start'
  | 'end'
  | 'yearOffset'
  | 'at'
  | 'time'

/** The inputs a kind shows, in order; `time` is optional wherever it appears. */
export function fieldsOf(kind: DeadlineKind): readonly DeadlineField[] {
  return KIND_FIELDS[kind]
}

// The same bounds as the API's checks, so a rule the form lets through is one the API takes.
const INTEGER_BOUNDS: Record<'n' | 'nth' | 'monthOffset' | 'day' | 'hours' | 'yearOffset', [number, number]> = {
  n: [1, 3660],
  nth: [1, 31],
  monthOffset: [-120, 120],
  day: [1, 28],
  hours: [1, 87840],
  yearOffset: [-10, 10],
}

const MONTH_DAY = /^(\d{2})-(\d{2})$/
const HOUR_MINUTE = /^([01]\d|2[0-3]):[0-5]\d$/
/** What an `<input type="datetime-local">` holds, with or without seconds. */
const LOCAL_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/

/** An input's text, or a number when a caller builds the values in code. */
const raw = z.union([z.string(), z.number()]).optional()

const formSchema = z.object({
  kind: z.enum(DEADLINE_KINDS as [DeadlineKind, ...DeadlineKind[]]),
  n: raw,
  monthOffset: raw,
  day: raw,
  hours: raw,
  roll: z.string().optional(),
  start: z.string().optional(),
  end: z.string().optional(),
  yearOffset: raw,
  at: z.string().optional(),
  time: z.string().optional(),
  anchor: z.string(),
  scope: z.enum(SCOPES as [HolidayScope, ...HolidayScope[]]),
})

/** What the form holds: the inputs' text. */
export type DeadlineFormValues = z.input<typeof formSchema>

/** A valid form: numbers where the rule takes integers, and only the kind's own fields. */
export interface DeadlineValues {
  kind: DeadlineKind
  n?: number
  monthOffset?: number
  day?: number
  hours?: number
  roll?: Roll
  start?: string
  end?: string
  yearOffset?: number
  at?: string
  time?: string
  anchor: string
  scope: HolidayScope
}

export const DEADLINE_DEFAULTS: DeadlineFormValues = {
  // The quarterly ROR: the tenth CNBV business day of the month after the quarter.
  kind: 'NTH_BUSINESS_DAY_OF',
  n: '10',
  monthOffset: '1',
  day: '17',
  hours: '24',
  roll: 'NEXT_BUSINESS_DAY',
  start: '01-01',
  end: '01-31',
  yearOffset: '0',
  at: '',
  time: '',
  anchor: '',
  scope: 'CNBV',
}

function toInteger(value: string | number | undefined): number | undefined {
  const text = typeof value === 'number' ? String(value) : (value ?? '').trim()
  return /^-?\d+$/.test(text) ? Number(text) : undefined
}

function isMonthDay(value: string | undefined): boolean {
  const match = MONTH_DAY.exec(value ?? '')
  if (match === null) return false
  const month = Number(match[1])
  const day = Number(match[2])
  // A common year, as the API checks: 29 February has no date three years out of four.
  const date = new Date(Date.UTC(2001, month - 1, day))
  return date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

function isLocalDateTime(value: string | undefined): boolean {
  return LOCAL_DATETIME.test(value ?? '') && !Number.isNaN(new Date(value ?? '').getTime())
}

/** Which integer bound a field of a kind is checked against. */
function boundOf(kind: DeadlineKind, field: DeadlineField): keyof typeof INTEGER_BOUNDS | undefined {
  if (field === 'n') return kind === 'NTH_BUSINESS_DAY_OF' ? 'nth' : 'n'
  if (field === 'monthOffset' || field === 'day' || field === 'hours' || field === 'yearOffset') return field
  return undefined
}

/** The message for an integer outside its bounds: offsets may be negative, counts may not. */
function integerMessage(field: DeadlineField): ValidationKey {
  return field === 'monthOffset' || field === 'yearOffset' ? 'invalid' : 'positiveInteger'
}

export const deadlineSchema = formSchema
  .superRefine((values, ctx) => {
    const problem = (path: string, key: ValidationKey) => ctx.addIssue({ code: 'custom', path: [path], message: msgKey(key) })
    if (!isLocalDateTime(values.anchor)) problem('anchor', 'dateFormat')
    for (const field of KIND_FIELDS[values.kind]) {
      const bound = boundOf(values.kind, field)
      if (bound !== undefined) {
        const value = toInteger(values[field as 'n'])
        const [low, high] = INTEGER_BOUNDS[bound]
        if (value === undefined || value < low || value > high) problem(field, integerMessage(field))
      } else if (field === 'roll') {
        if (!(ROLLS as readonly string[]).includes(values.roll ?? '')) problem(field, 'invalid')
      } else if (field === 'start' || field === 'end') {
        if (!isMonthDay(values[field])) problem(field, 'dateFormat')
      } else if (field === 'at') {
        if (!isLocalDateTime(values.at)) problem(field, 'dateFormat')
      } else if ((values.time ?? '') !== '' && !HOUR_MINUTE.test(values.time ?? '')) {
        problem(field, 'dateFormat')
      }
    }
  })
  .transform((values): DeadlineValues => {
    const out: DeadlineValues = { kind: values.kind, anchor: values.anchor, scope: values.scope }
    for (const field of KIND_FIELDS[values.kind]) {
      if (boundOf(values.kind, field) !== undefined) {
        out[field as 'n'] = toInteger(values[field as 'n'])
      } else if (field === 'roll') {
        out.roll = values.roll as Roll
      } else if ((values[field as 'time'] ?? '') !== '') {
        out[field as 'time'] = values[field as 'time']
      }
    }
    return out
  })

function need<T>(value: T | undefined, field: DeadlineField): T {
  if (value === undefined) throw new Error(`the deadline form lacks ${field}`)
  return value
}

/**
 * `2025-12-31T23:59` as typed in a datetime-local input, read in the viewer's time zone,
 * with that zone's offset: `2025-12-31T23:59:00-06:00`. The API refuses a naive anchor.
 */
export function toAnchor(local: string): string {
  const withSeconds = local.length === 16 ? `${local}:00` : local
  const minutes = -new Date(withSeconds).getTimezoneOffset()
  const sign = minutes < 0 ? '-' : '+'
  const hours = String(Math.floor(Math.abs(minutes) / 60)).padStart(2, '0')
  const rest = String(Math.abs(minutes) % 60).padStart(2, '0')
  return `${withSeconds}${sign}${hours}:${rest}`
}

/** The API's `DeadlineRule` for a valid form: `{kind, params, tz}`, params in snake case. */
export function toDeadlineRule(values: DeadlineValues): DeadlineRule {
  const params: DeadlineRule['params'] = {}
  for (const field of KIND_FIELDS[values.kind]) {
    switch (field) {
      case 'n':
        params.n = need(values.n, field)
        break
      case 'monthOffset':
        params.month_offset = need(values.monthOffset, field)
        break
      case 'day':
        params.day = need(values.day, field)
        break
      case 'hours':
        params.hours = need(values.hours, field)
        break
      case 'roll':
        params.roll = need(values.roll, field)
        break
      case 'start':
        params.start = need(values.start, field)
        break
      case 'end':
        params.end = need(values.end, field)
        break
      case 'yearOffset':
        params.year_offset = need(values.yearOffset, field)
        break
      case 'at':
        params.at = toAnchor(need(values.at, field))
        break
      case 'time':
        if (values.time !== undefined) params.time = values.time
        break
    }
  }
  return { kind: values.kind, params, tz: DEADLINE_TZ }
}
