import type { Locale } from '@/i18n'

/**
 * Locale-aware formatting through `Intl`. Every function takes the active locale
 * explicitly (components get it from `useFormat`), so output follows the language
 * switcher. Formatters are cached per locale because constructing them is costly.
 */

const dateTimeFormats = new Map<Locale, Intl.DateTimeFormat>()
const dateFormats = new Map<Locale, Intl.DateTimeFormat>()
const shortDateFormats = new Map<Locale, Intl.DateTimeFormat>()
const timeFormats = new Map<Locale, Intl.DateTimeFormat>()
const numberFormats = new Map<Locale, Intl.NumberFormat>()
const regionNames = new Map<Locale, Intl.DisplayNames>()

function cached<T>(cache: Map<Locale, T>, locale: Locale, create: () => T): T {
  const hit = cache.get(locale)
  if (hit !== undefined) return hit
  const made = create()
  cache.set(locale, made)
  return made
}

function toDate(value: string | Date): Date {
  return value instanceof Date ? value : new Date(value)
}

/** "18 oct 2026, 16:24" / "Oct 18, 2026, 4:24 PM" */
export function formatDateTime(value: string | Date, locale: Locale): string {
  const format = cached(
    dateTimeFormats,
    locale,
    () => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }),
  )
  return format.format(toDate(value))
}

/** "18 de octubre de 2026" / "October 18, 2026" */
export function formatDate(value: string | Date, locale: Locale): string {
  const format = cached(dateFormats, locale, () => new Intl.DateTimeFormat(locale, { dateStyle: 'long' }))
  return format.format(toDate(value))
}

/** "18 oct 2026" / "Oct 18, 2026": the date half of `formatDateTime`, for compact cells. */
export function formatShortDate(value: string | Date, locale: Locale): string {
  const format = cached(shortDateFormats, locale, () => new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }))
  return format.format(toDate(value))
}

/** "16:24" / "4:24 PM": the time half of `formatDateTime`. */
export function formatTime(value: string | Date, locale: Locale): string {
  const format = cached(timeFormats, locale, () => new Intl.DateTimeFormat(locale, { timeStyle: 'short' }))
  return format.format(toDate(value))
}

export function formatNumber(value: number, locale: Locale): string {
  const format = cached(numberFormats, locale, () => new Intl.NumberFormat(locale))
  return format.format(value)
}

/** The country's name in the active language: "MX" becomes "México" / "Mexico". */
export function formatRegion(code: string, locale: Locale): string {
  const names = cached(regionNames, locale, () => new Intl.DisplayNames(locale, { type: 'region' }))
  try {
    return names.of(code.toUpperCase()) ?? code
  } catch {
    return code // not a valid region code
  }
}
