import { describe, expect, it } from 'vitest'
import { dayAsLocalDate, formatMicros, isoDay, usageWindow } from './format'

describe('isoDay', () => {
  it('is the calendar day in UTC', () => {
    expect(isoDay(new Date('2026-10-19T03:00:00Z'))).toBe('2026-10-19')
    expect(isoDay(new Date('2026-10-18T23:59:59Z'))).toBe('2026-10-18')
  })
})

describe('usageWindow', () => {
  it('ends today and spans the asked number of days, both ends included', () => {
    const now = new Date('2026-10-19T12:00:00Z')
    expect(usageWindow(7, now)).toEqual({ from: '2026-10-13', to: '2026-10-19' })
    expect(usageWindow(30, now)).toEqual({ from: '2026-09-20', to: '2026-10-19' })
  })
})

describe('formatMicros', () => {
  it('shows micros as currency units with up to six fraction digits', () => {
    expect(formatMicros(2000, 'es-MX')).toBe('0.002')
    expect(formatMicros(1_234_567, 'en')).toBe('1.234567')
    expect(formatMicros(1, 'en')).toBe('0.000001')
    expect(formatMicros(0, 'es-MX')).toBe('0')
  })
})

describe('dayAsLocalDate', () => {
  it('is local midnight of that calendar day, whatever the time zone', () => {
    const date = dayAsLocalDate('2026-10-18')
    expect([date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()]).toEqual([2026, 9, 18, 0])
  })
})
