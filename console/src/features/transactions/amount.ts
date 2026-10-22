import type { Locale } from '@/i18n'

/** Minor units per major unit of the currencies the API accepts (MXN and USD, plan Deviation 6). */
const MINOR_DIGITS = 2

/** "12,500.00 MXN": an amount in minor units with its currency code, in the active locale. */
export function formatMinor(minor: number, currency: string, locale: Locale): string {
  const major = new Intl.NumberFormat(locale, {
    minimumFractionDigits: MINOR_DIGITS,
    maximumFractionDigits: MINOR_DIGITS,
  }).format(minor / 10 ** MINOR_DIGITS)
  return `${major} ${currency}`
}
