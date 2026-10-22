import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { useRevealTransaction } from './queries'

/** The page's one reveal: the counterparty card and the payload card show the same answer. */
export type TransactionReveal = ReturnType<typeof useRevealTransaction>

/** "Revelar" while nothing is revealed, "Ocultar" (which drops the plaintext) once it is. */
export function RevealAction({ reveal }: { reveal: TransactionReveal }) {
  const { t } = useTranslation()
  return reveal.isSuccess ? (
    <Button type="button" variant="outline" size="sm" onClick={() => reveal.reset()}>
      <EyeOff aria-hidden />
      {t('transactions.detail.reveal.hide')}
    </Button>
  ) : (
    <Button type="button" size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate()}>
      <Eye aria-hidden />
      {reveal.isPending ? t('transactions.detail.reveal.running') : t('transactions.detail.reveal.button')}
    </Button>
  )
}

/** The `VIEW_PII` notice shown beside revealed values. */
export function RevealNotice() {
  const { t } = useTranslation()
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <ShieldCheck className="size-4" aria-hidden />
      {t('transactions.detail.revealNotice')}
    </p>
  )
}

/**
 * The transaction exactly as the client sent it (`raw`), behind the audited reveal. It is
 * shown as pretty-printed JSON text, never as markup.
 */
export function RawPayloadCard({ reveal }: { reveal: TransactionReveal }) {
  const { t } = useTranslation()
  const title = t('transactions.detail.raw.title')
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{t('transactions.detail.raw.description')}</CardDescription>
        <CardAction>
          <RevealAction reveal={reveal} />
        </CardAction>
      </CardHeader>
      <CardContent>
        <div aria-live="polite">
          {reveal.isSuccess && (
            <div className="space-y-3">
              <RevealNotice />
              <pre className="max-h-96 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs whitespace-pre-wrap break-words">
                {JSON.stringify(reveal.data.raw, null, 2)}
              </pre>
            </div>
          )}
        </div>
        {reveal.isError && <ErrorBlock error={reveal.error} onRetry={() => reveal.mutate()} />}
      </CardContent>
    </Card>
  )
}
