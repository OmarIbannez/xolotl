import { History } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { TransactionStaffView } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { useFormat } from '@/i18n/use-locale'
import { BackfillDialog } from './BackfillDialog'
import { formatMinor } from './amount'
import { MexicoCityTimeCell } from './MexicoCityTimeCell'
import { useBackfillTransactions } from './queries'
import { TransactionStatusBadge } from './TransactionStatusBadge'

/**
 * The transactions that arrived after the receipt window and wait for a backfill, and (MLRO,
 * ADMIN) "Ejecutar backfill", which asks the worker to process a range of them.
 */
export function BackfillTab() {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const format = useFormat()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [requesting, setRequesting] = useState(false)
  const pending = useBackfillTransactions(cursor)
  const page = pending.data

  const columns: Column<TransactionStaffView>[] = [
    {
      id: 'externalId',
      header: t('transactions.table.externalId'),
      cell: (row) => <span className="font-mono text-xs">{row.external_id}</span>,
    },
    { id: 'occurredAt', header: t('transactions.table.occurredAt'), cell: (row) => <MexicoCityTimeCell value={row.occurred_at} /> },
    { id: 'receivedAt', header: t('transactions.table.receivedAt'), cell: (row) => <MexicoCityTimeCell value={row.received_at} /> },
    {
      id: 'party',
      header: t('transactions.table.customer'),
      cell: (row) => (
        <Link to={`/parties/${row.party_id}`} className="text-xs underline-offset-4 hover:underline">
          {t('transactions.table.openParty')}
        </Link>
      ),
    },
    {
      id: 'amount',
      header: t('transactions.table.amount'),
      cell: (row) => <span className="tabular">{formatMinor(row.amount_minor, row.currency, format.locale)}</span>,
      className: 'text-right',
    },
    { id: 'status', header: t('transactions.table.status'), cell: (row) => <TransactionStatusBadge status={row.status} /> },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">{t('transactions.backfill.description')}</p>
        {hasRole(ACCESS.backfill) && (
          <Button type="button" variant="outline" size="sm" onClick={() => setRequesting(true)}>
            <History aria-hidden />
            {t('transactions.backfill.run')}
          </Button>
        )}
      </div>
      {pending.isError ? (
        <ErrorBlock error={pending.error} onRetry={() => void pending.refetch()} />
      ) : (
        <DataTable
          label={t('transactions.backfill.label')}
          columns={columns}
          rows={page?.items}
          getRowId={(row) => row.id}
          empty={t('transactions.backfill.empty')}
        />
      )}
      {(cursor !== undefined || (page !== undefined && page.items.length > 0)) && (
        <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={pending.isFetching} />
      )}
      {requesting && <BackfillDialog onClose={() => setRequesting(false)} />}
    </div>
  )
}
