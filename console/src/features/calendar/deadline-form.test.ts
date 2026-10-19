import { describe, expect, it } from 'vitest'
import { DEADLINE_DEFAULTS, deadlineSchema, toAnchor, toDeadlineRule, type DeadlineFormValues } from './deadline-form'

const base: DeadlineFormValues = { ...DEADLINE_DEFAULTS, anchor: '2025-12-31T23:59', scope: 'CNBV' }

/** What the form would report: where, and the catalogue key of the message. */
function problems(input: Partial<DeadlineFormValues>) {
  const result = deadlineSchema.safeParse({ ...base, ...input })
  return result.success ? [] : result.error.issues.map((issue) => ({ at: issue.path.join('.'), key: issue.message }))
}

function rule(input: Partial<DeadlineFormValues>) {
  return toDeadlineRule(deadlineSchema.parse({ ...base, ...input }))
}

describe('toDeadlineRule', () => {
  it('builds NTH_BUSINESS_DAY_OF from the month offset and n', () => {
    expect(
      toDeadlineRule({ kind: 'NTH_BUSINESS_DAY_OF', monthOffset: 1, n: 10, anchor: '2025-12-31T23:59', scope: 'CNBV' }),
    ).toEqual({ kind: 'NTH_BUSINESS_DAY_OF', params: { month_offset: 1, n: 10 }, tz: 'America/Mexico_City' })
  })

  it('builds DAY_OF_NEXT_MONTH from the day and the roll', () => {
    expect(rule({ kind: 'DAY_OF_NEXT_MONTH', day: '17', roll: 'NEXT_BUSINESS_DAY' })).toEqual({
      kind: 'DAY_OF_NEXT_MONTH',
      params: { day: 17, roll: 'NEXT_BUSINESS_DAY' },
      tz: 'America/Mexico_City',
    })
  })

  it('builds HOURS_AFTER from the hours alone', () => {
    expect(rule({ kind: 'HOURS_AFTER', hours: '24', time: '10:00' })).toEqual({
      kind: 'HOURS_AFTER',
      params: { hours: 24 },
      tz: 'America/Mexico_City',
    })
  })

  it('carries an optional time and drops the fields of other kinds', () => {
    expect(rule({ kind: 'BUSINESS_DAYS_AFTER', n: '3', time: '18:00', day: '31' })).toEqual({
      kind: 'BUSINESS_DAYS_AFTER',
      params: { n: 3, time: '18:00' },
      tz: 'America/Mexico_City',
    })
  })

  it('builds DATE_RANGE and FIXED', () => {
    expect(rule({ kind: 'DATE_RANGE', start: '01-01', end: '05-17', yearOffset: '1' }).params).toEqual({
      start: '01-01',
      end: '05-17',
      year_offset: 1,
    })
    expect(rule({ kind: 'FIXED', at: '2026-03-31T18:00' }).params).toEqual({ at: '2026-03-31T18:00:00-06:00' })
  })
})

describe('deadlineSchema', () => {
  it('accepts the defaults', () => {
    expect(problems({})).toEqual([])
  })

  it('refuses a day of 31: the API caps DAY_OF_NEXT_MONTH at 28', () => {
    expect(problems({ kind: 'DAY_OF_NEXT_MONTH', day: '31', roll: 'NONE' })).toEqual([
      { at: 'day', key: 'positiveInteger' },
    ])
    expect(deadlineSchema.safeParse({ ...base, kind: 'DAY_OF_NEXT_MONTH', day: 31, roll: 'NONE' }).success).toBe(false)
  })

  it('refuses a missing or fractional count, and a negative one', () => {
    expect(problems({ kind: 'BUSINESS_DAYS_AFTER', n: '' })).toEqual([{ at: 'n', key: 'positiveInteger' }])
    expect(problems({ kind: 'BUSINESS_DAYS_AFTER', n: '1.5' })).toEqual([{ at: 'n', key: 'positiveInteger' }])
    expect(problems({ kind: 'HOURS_AFTER', hours: '-1' })).toEqual([{ at: 'hours', key: 'positiveInteger' }])
  })

  it('checks the shape of month-days, times and datetimes', () => {
    expect(problems({ kind: 'DATE_RANGE', start: '1-1', end: '02-30', yearOffset: '0' })).toEqual([
      { at: 'start', key: 'dateFormat' },
      { at: 'end', key: 'dateFormat' },
    ])
    expect(problems({ time: '25:00' })).toEqual([{ at: 'time', key: 'dateFormat' }])
    expect(problems({ anchor: '' })).toEqual([{ at: 'anchor', key: 'dateFormat' }])
    expect(problems({ kind: 'FIXED', at: 'tomorrow' })).toEqual([{ at: 'at', key: 'dateFormat' }])
  })

  it('ignores the fields the kind does not take', () => {
    expect(problems({ kind: 'HOURS_AFTER', hours: '24', day: '99', time: 'nope' })).toEqual([])
  })
})

describe('toAnchor', () => {
  it('gives the typed local time its offset', () => {
    // The suite runs in America/Mexico_City (vite.config.ts), UTC-6 all year.
    expect(toAnchor('2025-12-31T23:59')).toBe('2025-12-31T23:59:00-06:00')
  })
})
