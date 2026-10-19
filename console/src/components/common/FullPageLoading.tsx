import { useTranslation } from 'react-i18next'
import { Skeleton } from '@/components/ui/skeleton'
import { LogoMark } from './Logo'

/** What the app shows before it knows whether a stored session is still good. */
export function FullPageLoading() {
  const { t } = useTranslation()
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={t('common.loading')}
      className="grid min-h-dvh place-items-center bg-background"
    >
      <div className="flex w-64 flex-col items-center gap-4">
        <LogoMark className="size-10" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </div>
  )
}
