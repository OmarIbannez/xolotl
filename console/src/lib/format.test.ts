import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime, formatNumber, formatRegion, formatShortDate, formatTime } from './format'

const MIDDAY = '2026-10-18T12:00:00Z'

describe('formatting follows the active locale', () => {
  it('dates', () => {
    expect(formatDate(MIDDAY, 'es-MX')).toBe('18 de octubre de 2026')
    expect(formatDate(MIDDAY, 'en')).toBe('October 18, 2026')
    expect(formatDate(new Date(MIDDAY), 'en')).toBe('October 18, 2026')
  })

  it('date and time', () => {
    expect(formatDateTime(MIDDAY, 'es-MX')).toMatch(/oct/i)
    expect(formatDateTime(MIDDAY, 'en')).toMatch(/Oct/)
  })

  it('the date and the time on their own, which together say what the date and time say', () => {
    expect(formatShortDate(MIDDAY, 'es-MX')).toMatch(/18 oct/i)
    expect(formatShortDate(MIDDAY, 'en')).toMatch(/Oct 18, 2026/)
    expect(formatShortDate(new Date(MIDDAY), 'en')).toBe(formatShortDate(MIDDAY, 'en'))
    for (const locale of ['es-MX', 'en'] as const) {
      expect(formatTime(MIDDAY, locale)).toMatch(/\d{1,2}:\d{2}/)
      const both = formatDateTime(MIDDAY, locale)
      expect(both).toContain(formatShortDate(MIDDAY, locale))
      expect(both).toContain(formatTime(MIDDAY, locale))
    }
  })

  it('numbers', () => {
    expect(formatNumber(1234567.5, 'en')).toBe('1,234,567.5')
    expect(formatNumber(1234567.5, 'es-MX')).toBe('1,234,567.5')
  })

  it('country names', () => {
    expect(formatRegion('MX', 'es-MX')).toBe('México')
    expect(formatRegion('MX', 'en')).toBe('Mexico')
    expect(formatRegion('mx', 'en')).toBe('Mexico')
  })

  it('leaves an invalid country code as it came', () => {
    expect(formatRegion('not-a-region', 'en')).toBe('not-a-region')
  })
})
