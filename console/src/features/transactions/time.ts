import type { Locale } from '@/i18n'

/** The zone the API reads `from` and `to` in (Mexico City days), so times show in it too. */
export const MEXICO_CITY = 'America/Mexico_City'

const formats = new Map<string, Intl.DateTimeFormat>()

/** A cached `Intl.DateTimeFormat` for `locale` and `options`, always in Mexico City time. */
export function mexicoCityFormat(locale: Locale, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${JSON.stringify(options)}`
  const hit = formats.get(key)
  if (hit !== undefined) return hit
  const made = new Intl.DateTimeFormat(locale, { ...options, timeZone: MEXICO_CITY })
  formats.set(key, made)
  return made
}

/** "20 oct 2026, 20:00": an instant as a Mexico City date and time. */
export function mexicoCityDateTime(value: string, locale: Locale): string {
  return mexicoCityFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

/** "20 oct 2026": the Mexico City date of an instant. */
export function mexicoCityDate(value: string, locale: Locale): string {
  return mexicoCityFormat(locale, { dateStyle: 'medium' }).format(new Date(value))
}

/** "20:00": the Mexico City time of an instant. */
export function mexicoCityTime(value: string, locale: Locale): string {
  return mexicoCityFormat(locale, { timeStyle: 'short' }).format(new Date(value))
}

/** Today as a Mexico City day (`YYYY-MM-DD`): a backfill range cannot end after it. */
export function mexicoCityToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: MEXICO_CITY }).format(now)
}
