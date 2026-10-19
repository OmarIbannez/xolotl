import { describe, expect, it } from 'vitest'
import { formatTimestamp, formatTimestampParts } from './format'

const INSTANT = '2026-10-18T16:24:05.123456Z'

describe('formatTimestamp', () => {
  it('reads the instant in the given time zone, to the second, and names the zone', () => {
    const utc = formatTimestamp(INSTANT, 'en', 'UTC')
    expect(utc).toContain('Oct 18, 2026')
    expect(utc).toMatch(/4:24:05\s?PM/)
    expect(utc).toContain('UTC')

    const mexico = formatTimestamp(INSTANT, 'en', 'America/Mexico_City') // UTC-6 all year since 2022
    expect(mexico).toMatch(/10:24:05\s?AM/)
    expect(mexico).toMatch(/CST|GMT-6/)
  })

  it('follows the language', () => {
    expect(formatTimestamp(INSTANT, 'es-MX', 'UTC')).toMatch(/18 oct 2026/i)
    expect(formatTimestamp(INSTANT, 'es-MX', 'UTC')).toMatch(/4:24:05\s?p\.\s?m\./i)
    expect(formatTimestamp(INSTANT, 'es-MX', 'UTC')).toContain('UTC')
  })

  it('uses the browser time zone when none is given, and still names one', () => {
    expect(formatTimestamp(INSTANT, 'en')).toMatch(/\d{1,2}:24:05/)
    expect(formatTimestamp(INSTANT, 'en')).toMatch(/[A-Z]{2,5}|GMT[+-−]?\d{1,2}/)
  })

  it('accepts a Date', () => {
    expect(formatTimestamp(new Date(INSTANT), 'en', 'UTC')).toBe(formatTimestamp(INSTANT, 'en', 'UTC'))
  })

  it('hands back what it cannot read instead of throwing', () => {
    expect(formatTimestamp('not a date', 'en', 'UTC')).toBe('not a date')
  })
})

describe('formatTimestampParts', () => {
  it('splits the instant into the date and the time with its zone', () => {
    expect(formatTimestampParts(INSTANT, 'en', 'UTC')).toEqual({ date: 'Oct 18, 2026', time: expect.stringMatching(/^4:24:05\s?PM UTC$/) })
    const es = formatTimestampParts(INSTANT, 'es-MX', 'America/Mexico_City')
    expect(es.date).toMatch(/^18 oct 2026$/i)
    expect(es.time).toMatch(/^10:24:05\s?a\.\s?m\.\s+\S+$/i)
  })

  it('carries the same information as the one-line form', () => {
    for (const locale of ['en', 'es-MX'] as const) {
      const { date, time } = formatTimestampParts(INSTANT, locale, 'UTC')
      expect(formatTimestamp(INSTANT, locale, 'UTC')).toBe(`${date}, ${time}`)
    }
  })

  it('hands back what it cannot read instead of throwing', () => {
    expect(formatTimestampParts('not a date', 'en')).toEqual({ date: 'not a date', time: '' })
  })
})
