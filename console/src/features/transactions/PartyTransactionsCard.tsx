import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { TransactionStaffView } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { AmountCell, StatusCell } from './cells'
import { staffRows, useTransactions } from './queries'

/**
 * Cliente › Transacciones: the party's latest transactions (one page), each linking to its
 * detail, and "Ver todas" to the whole list narrowed to the party.
 */
export function PartyTransactionsCard({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const transactions = useTransactions({ party_id: partyId }, undefined)
  const rows = transactions.data === undefined ? undefined : staffRows(transactions.data.items)

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
    {
      id: 'status',
      header: t('transactions.table.status'),
      cell: (row) => <StatusCell status={row.status} late={row.late} />,
    },
  ]

  return (
    <Card className="animate-fade-in-up">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{t('parties.transactions.title')}</CardTitle>
        <Button asChild variant="outline" size="sm">
          <Link to={`/transactions?party_id=${encodeURIComponent(partyId)}`}>{t('parties.transactions.viewAll')}</Link>
        </Button>
      </CardHeader>
      <CardContent>
        {transactions.isError ? (
          <ErrorBlock error={transactions.error} onRetry={() => void transactions.refetch()} />
        ) : (
          <DataTable
            label={t('parties.transactions.label')}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.id}
            empty={t('parties.transactions.empty')}
          />
        )}
      </CardContent>
    </Card>
  )
}
