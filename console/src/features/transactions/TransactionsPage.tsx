import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import type { TransactionStaffView } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { BackfillTab } from './BackfillTab'
import { BatchesCard } from './BatchesCard'
import { AmountCell, StatusCell } from './cells'
import { FilesCard } from './FilesCard'
import { ParkedTab } from './ParkedTab'
import { staffRows, useTransactions } from './queries'

const TABS = ['all', 'parked', 'backfill'] as const
type Tab = (typeof TABS)[number]
const isTab = (value: string): value is Tab => (TABS as readonly string[]).includes(value)

/** Every transaction, newest first, narrowed to one party when the URL names it (`?party_id=`). */
function AllTab() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const partyId = params.get('party_id') ?? undefined
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const transactions = useTransactions({ party_id: partyId }, cursor)
  const page = transactions.data

  const columns: Column<TransactionStaffView>[] = [
    {
      id: 'externalId',
      header: t('transactions.table.externalId'),
      cell: (row) => (
        <Link to={`/transactions/${row.id}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.external_id}
        </Link>
      ),
    },
    { id: 'occurredAt', header: t('transactions.table.occurredAt'), cell: (row) => <DateTimeCell value={row.occurred_at} /> },
    {
      id: 'instrument',
      header: t('transactions.table.instrument'),
      cell: (row) => (
        <span className="font-mono text-xs">
          {row.instrument} · {row.rail}
        </span>
      ),
    },
    {
      id: 'amount',
      header: t('transactions.table.amount'),
      cell: (row) => <AmountCell minor={row.amount_minor} currency={row.currency} />,
      className: 'text-right',
    },
    { id: 'status', header: t('transactions.table.status'), cell: (row) => <StatusCell status={row.status} late={row.late} /> },
  ]

  return (
    <div className="space-y-4">
      {transactions.isError ? (
        <ErrorBlock error={transactions.error} onRetry={() => void transactions.refetch()} />
      ) : (
        <DataTable
          label={t('transactions.table.label')}
          columns={columns}
          rows={page === undefined ? undefined : staffRows(page.items)}
          getRowId={(row) => row.id}
          empty={t('transactions.table.empty')}
        />
      )}
      {(cursor !== undefined || (page !== undefined && page.items.length > 0)) && (
        <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={transactions.isFetching} />
      )}
    </div>
  )
}

/**
 * Transacciones: the files and batches panels, then the transactions by tab: all of them, the
 * parked ones and those awaiting a backfill (with "Ejecutar backfill" for MLRO and ADMIN).
 */
export function TransactionsPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('all')

  return (
    <>
      <PageHeader title={t('transactions.title')} description={t('transactions.description')} />
      <div className="space-y-6">
        <div className="grid gap-6 lg:grid-cols-2">
          <FilesCard />
          <BatchesCard />
        </div>
        <Card className="animate-fade-in-up">
          <Tabs value={tab} onValueChange={(value) => isTab(value) && setTab(value)}>
            <CardHeader>
              <TabsList aria-label={t('transactions.tabs.label')}>
                {TABS.map((value) => (
                  <TabsTrigger key={value} value={value}>
                    {t(`transactions.tabs.${value}`)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </CardHeader>
            <CardContent>
              <TabsContent value="all">
                <AllTab />
              </TabsContent>
              <TabsContent value="parked">
                <ParkedTab />
              </TabsContent>
              <TabsContent value="backfill">
                <BackfillTab />
              </TabsContent>
            </CardContent>
          </Tabs>
        </Card>
      </div>
    </>
  )
}
