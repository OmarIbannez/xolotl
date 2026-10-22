import { useTranslation } from 'react-i18next'
import { ErrorBlock } from '@/components/common/states'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { HitsTable } from './HitsTable'
import { useTransactionHits } from './queries'

/** The hits a transaction is linked to, in any role (spec section 17), each opening its detail. */
export function TransactionHitsCard({ transactionId }: { transactionId: string }) {
  const { t } = useTranslation()
  const hits = useTransactionHits(transactionId)
  const title = t('hits.transaction.title')
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {hits.isError ? (
          <ErrorBlock error={hits.error} onRetry={() => void hits.refetch()} />
        ) : (
          <HitsTable label={t('hits.transaction.label')} rows={hits.data?.items} empty={t('hits.transaction.empty')} />
        )}
      </CardContent>
    </Card>
  )
}
