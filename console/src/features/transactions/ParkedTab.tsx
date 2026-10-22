import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TransactionStaffView } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { ErrorBlock } from '@/components/common/states'
import { calendarDay } from '@/features/parties/dates'
import { useFormat } from '@/i18n/use-locale'
import { AmountCell } from './cells'
import { useParkedTransactions } from './queries'

/**
 * The parked transactions: why each waits (a unit value or a policy), the unit and the date it
 * needs, and how many times the sweep has tried it. Nothing here acts on them: the sweep retries
 * them on its own once what they wait for exists.
 */
export function ParkedTab() {
  const { t } = useTranslation()
  const format = useFormat()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const parked = useParkedTransactions(cursor)
  const page = parked.data

  const columns: Column<TransactionStaffView>[] = [
    {
      id: 'externalId',
      header: t('transactions.table.externalId'),
      cell: (row) => <span className="font-mono text-xs">{row.external_id}</span>,
    },
    { id: 'occurredAt', header: t('transactions.table.occurredAt'), cell: (row) => <DateTimeCell value={row.occurred_at} /> },
    {
      id: 'amount',
      header: t('transactions.table.amount'),
      cell: (row) => <AmountCell minor={row.amount_minor} currency={row.currency} />,
      className: 'text-right',
    },
    {
      id: 'reason',
      header: t('transactions.parked.reason'),
      cell: (row) => (row.parked === null ? '—' : t(`transactions.parkReason.${row.parked.reason}`)),
    },
    {
      id: 'unit',
      header: t('transactions.parked.unit'),
      cell: (row) => <span className="font-mono text-xs">{row.parked?.unit_code ?? '—'}</span>,
    },
    {
      id: 'neededOn',
      header: t('transactions.parked.neededOn'),
      cell: (row) =>
        row.parked?.needed_on ? (
          <time dateTime={row.parked.needed_on}>{format.date(calendarDay(row.parked.needed_on))}</time>
        ) : (
          '—'
        ),
    },
    {
      id: 'attempts',
      header: t('transactions.parked.attempts'),
      cell: (row) => (row.parked === null ? '—' : format.number(row.parked.attempts)),
      className: 'text-right tabular-nums',
    },
  ]

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('transactions.parked.description')}</p>
      {parked.isError ? (
        <ErrorBlock error={parked.error} onRetry={() => void parked.refetch()} />
      ) : (
        <DataTable
          label={t('transactions.parked.label')}
          columns={columns}
          rows={page?.items}
          getRowId={(row) => row.id}
          empty={t('transactions.parked.empty')}
        />
      )}
      {(cursor !== undefined || (page !== undefined && page.items.length > 0)) && (
        <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={parked.isFetching} />
      )}
    </div>
  )
}
