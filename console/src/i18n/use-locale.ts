import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  formatDate,
  formatDateTime,
  formatNumber,
  formatRegion,
  formatShortDate,
  formatTime,
} from '@/lib/format'
import { DEFAULT_LOCALE, isLocale, type Locale } from './index'

/** The active locale; components using it re-render when the language changes. */
export function useLocale(): Locale {
  const { i18n } = useTranslation()
  return isLocale(i18n.resolvedLanguage) ? i18n.resolvedLanguage : DEFAULT_LOCALE
}

/** Date, number and country formatting bound to the active locale (through `Intl`). */
export function useFormat() {
  const locale = useLocale()
  return useMemo(
    () => ({
      locale,
      date: (value: string | Date) => formatDate(value, locale),
      dateTime: (value: string | Date) => formatDateTime(value, locale),
      shortDate: (value: string | Date) => formatShortDate(value, locale),
      time: (value: string | Date) => formatTime(value, locale),
      number: (value: number) => formatNumber(value, locale),
      region: (code: string) => formatRegion(code, locale),
    }),
    [locale],
  )
}
