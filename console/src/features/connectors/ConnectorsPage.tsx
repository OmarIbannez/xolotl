import { useTranslation } from 'react-i18next'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Card, CardContent } from '@/components/ui/card'
import { ConnectorsTable } from './ConnectorsTable'
import { useConnectors } from './queries'
import { UsageCard } from './UsageCard'

/** The external verification services (ADMIN): mode, routes, circuit state, credentials and usage. */
export function ConnectorsPage() {
  const { t } = useTranslation()
  const connectors = useConnectors()

  return (
    <>
      <PageHeader title={t('connectors.title')} description={t('connectors.description')} />

      <div className="space-y-6">
        <Card className="animate-fade-in-up">
          <CardContent>
            {connectors.isError ? (
              <ErrorBlock error={connectors.error} onRetry={() => void connectors.refetch()} />
            ) : (
              <ConnectorsTable rows={connectors.data?.items} loading={connectors.isPending} />
            )}
          </CardContent>
        </Card>

        <UsageCard />
      </div>
    </>
  )
}
