import type { Locale } from '@/i18n'

/** The usage windows the page offers, in days ending today (UTC). */
export type UsageRange = 7 | 30

const DAY_MS = 24 * 60 * 60 * 1000

/** The calendar day of `d` in UTC, as 'YYYY-MM-DD' (the API's days are UTC days). */
export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** The inclusive window of `days` UTC days that ends on today's. */
export function usageWindow(days: UsageRange, now: Date): { from: string; to: string } {
  return { from: isoDay(new Date(now.getTime() - (days - 1) * DAY_MS)), to: isoDay(now) }
}

/**
 * A 'YYYY-MM-DD' day as local midnight of that same calendar day, so a date formatter shows
 * that day in every time zone (`new Date('2026-10-18')` is UTC midnight, which is still
 * 17 October in Mexico City).
 */
export function dayAsLocalDate(day: string): Date {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number)
  return new Date(year, month - 1, date)
}

const microFormats = new Map<Locale, Intl.NumberFormat>()

/**
 * A cost in micros (millionths of the currency unit) as units with up to six fraction digits.
 * `useFormat().number` keeps Intl's default of three, which would show one micro as '0'.
 */
export function formatMicros(costMicros: number, locale: Locale): string {
  let format = microFormats.get(locale)
  if (format === undefined) {
    format = new Intl.NumberFormat(locale, { maximumFractionDigits: 6 })
    microFormats.set(locale, format)
  }
  return format.format(costMicros / 1_000_000)
}
