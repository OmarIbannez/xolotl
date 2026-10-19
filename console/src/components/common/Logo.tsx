import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

/** The xolotl mark: an "x" between two gills. Same drawing as public/favicon.svg. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={cn('size-6 shrink-0', className)}>
      <rect width="24" height="24" rx="6" className="fill-primary" />
      <path
        d="M7.5 7.5l9 9M16.5 7.5l-9 9"
        className="stroke-primary-foreground"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      <circle cx="5.2" cy="12" r="1.1" className="fill-primary-foreground" />
      <circle cx="18.8" cy="12" r="1.1" className="fill-primary-foreground" />
    </svg>
  )
}

export function Logo({ className }: { className?: string }) {
  const { t } = useTranslation()
  return (
    <span className={cn('flex items-center gap-2', className)}>
      <LogoMark />
      <span className="text-base font-semibold tracking-tight">{t('app.name')}</span>
    </span>
  )
}
