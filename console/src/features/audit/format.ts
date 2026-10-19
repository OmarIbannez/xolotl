import type { Locale } from '@/i18n'

const formats = new Map<string, Intl.DateTimeFormat>()

function formatter(
  locale: Locale,
  timeZone: string | undefined,
  style: 'date' | 'time' | 'both',
): Intl.DateTimeFormat {
  const key = `${locale}|${timeZone ?? ''}|${style}`
  let format = formats.get(key)
  if (format === undefined) {
    format = new Intl.DateTimeFormat(locale, {
      ...(style === 'time' ? {} : { dateStyle: 'medium' }),
      ...(style === 'date' ? {} : { timeStyle: 'long' }),
      ...(timeZone === undefined ? {} : { timeZone }),
    })
    formats.set(key, format)
  }
  return format
}

function toDate(value: string | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * An instant down to the second in the viewer's own time zone, with the zone named:
 * "18 oct 2026, 10:24:05 CST" / "Oct 18, 2026, 10:24:05 AM CST". An audit trail is read to
 * place events in time, so unlike the console's other dates this one says which clock it is.
 * `timeZone` exists for tests; the console always uses the browser's.
 */
export function formatTimestamp(value: string | Date, locale: Locale, timeZone?: string): string {
  const date = toDate(value)
  // One unreadable timestamp must not take the whole table down.
  return date === null ? String(value) : formatter(locale, timeZone, 'both').format(date)
}

/** The same instant in two pieces, for a cell that stacks them: "18 oct 2026" over "10:24:05 CST". */
export function formatTimestampParts(
  value: string | Date,
  locale: Locale,
  timeZone?: string,
): { date: string; time: string } {
  const date = toDate(value)
  if (date === null) return { date: String(value), time: '' }
  return {
    date: formatter(locale, timeZone, 'date').format(date),
    time: formatter(locale, timeZone, 'time').format(date),
  }
}
