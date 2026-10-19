import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

/** Titles the browser tab "<page> · xolotl" for the lifetime of the calling component. */
export function useDocumentTitle(title: string | undefined): void {
  const { t } = useTranslation()
  useEffect(() => {
    document.title = title ? `${title} · ${t('app.name')}` : t('app.title')
  }, [title, t])
}
