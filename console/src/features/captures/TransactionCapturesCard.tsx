import { useTranslation } from 'react-i18next'
import { ErrorBlock } from '@/components/common/states'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CapturesTable } from './CapturesTable'
import { useTransactionCaptures } from './queries'

/** The reportable events a transaction was captured as (spec section 17), staff only. */
export function TransactionCapturesCard({ transactionId }: { transactionId: string }) {
  const { t } = useTranslation()
  const captures = useTransactionCaptures(transactionId)
  const title = t('captures.transaction.title')
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {captures.isError ? (
          <ErrorBlock error={captures.error} onRetry={() => void captures.refetch()} />
        ) : (
          <CapturesTable
            label={t('captures.transaction.label')}
            rows={captures.data?.items}
            empty={t('captures.transaction.empty')}
            showTransaction={false}
          />
        )}
      </CardContent>
    </Card>
  )
}
