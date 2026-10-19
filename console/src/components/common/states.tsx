import { AlertCircle, Inbox } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { describeError } from '@/i18n/errors'

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2" role="status" aria-busy="true" aria-label={t('common.loading')}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  )
}

/**
 * Shows a failed call as a message for people (never the server's English detail). With
 * `onRetry`, it also offers to try again.
 */
export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation()
  const { message, reference } = describeError(t, error)
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-critical/40 bg-critical/10 p-3 text-sm"
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p>{message}</p>
        {reference && (
          <p className="font-mono text-xs text-muted-foreground">
            {t('errors.reference', { id: reference })}
          </p>
        )}
      </div>
      {onRetry && (
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          {t('common.retry')}
        </Button>
      )}
    </div>
  )
}

export function EmptyBlock({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
      <Inbox className="size-5" aria-hidden />
      {children}
    </div>
  )
}
