import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SUPPORTED_LOCALES, isLocale, setLocale } from '@/i18n'
import { useLocale } from '@/i18n/use-locale'

/** es-MX / en. The choice is remembered by the browser (see src/i18n/index.ts). */
export function LanguageSwitcher() {
  const { t } = useTranslation()
  const locale = useLocale()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={t('header.language')} title={t('header.language')}>
          <Languages />
          <span className="hidden sm:inline" aria-hidden>
            {locale.slice(0, 2).toUpperCase()}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={locale}
          onValueChange={(next) => {
            if (isLocale(next)) setLocale(next)
          }}
        >
          {SUPPORTED_LOCALES.map((option) => (
            <DropdownMenuRadioItem key={option} value={option}>
              {t(`language.${option}`)}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
