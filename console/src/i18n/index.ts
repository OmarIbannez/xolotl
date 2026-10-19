import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import en from './en.json'
import esMX from './es-MX.json'

export const SUPPORTED_LOCALES = ['es-MX', 'en'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]
export const DEFAULT_LOCALE: Locale = 'es-MX'

/** The chosen language is a per-browser convenience, so it may live in localStorage. */
const LOCALE_STORAGE_KEY = 'xolotl.locale'

export function isLocale(value: unknown): value is Locale {
  return SUPPORTED_LOCALES.some((locale) => locale === value)
}

// `satisfies` in both directions fails the build when a key exists in one catalogue
// but not in the other, so every string is keyed in both locales.
const resources = {
  'es-MX': { translation: esMX satisfies typeof en },
  en: { translation: en satisfies typeof esMX },
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: SUPPORTED_LOCALES,
    // Catalogues are bundled, so initialise synchronously: no Suspense, no flash of keys.
    initAsync: false,
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: LOCALE_STORAGE_KEY,
      caches: ['localStorage'],
    },
  })

/** The active locale, always one of the supported ones. */
export function currentLocale(): Locale {
  const resolved = i18n.resolvedLanguage
  return isLocale(resolved) ? resolved : DEFAULT_LOCALE
}

export function setLocale(locale: Locale): void {
  void i18n.changeLanguage(locale)
}

function syncDocumentLanguage(): void {
  document.documentElement.lang = currentLocale()
}
i18n.on('languageChanged', syncDocumentLanguage)
syncDocumentLanguage()

export default i18n
