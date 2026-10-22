import { Ban, CircleCheck, FileText, Replace, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { ReportableEventStatus, ReportableEventSummary } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Badge } from '@/components/ui/badge'
import { MexicoCityTimeCell } from '@/features/transactions/MexicoCityTimeCell'
import { LateBadge } from '@/features/transactions/TransactionStatusBadge'

const STATUS: Record<ReportableEventStatus, { variant: 'info' | 'good' | 'muted'; icon: LucideIcon }> = {
  CAPTURED: { variant: 'info', icon: CircleCheck },
  INCLUDED: { variant: 'good', icon: FileText },
  VOIDED: { variant: 'muted', icon: Ban },
  SUPERSEDED: { variant: 'muted', icon: Replace },
}

/** A reportable event's status, with an icon and a label (never color alone). */
export function EventStatusBadge({ status }: { status: ReportableEventStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`captures.events.status.${status}`)}
    </Badge>
  )
}

interface CapturesTableProps {
  label: string
  rows: readonly ReportableEventSummary[] | undefined
  loading?: boolean
  empty: string
  /** False on a transaction's own page, where every row is the same transaction. */
  showTransaction?: boolean
}

/** Reportable events as rows: capture, report, kind, transaction, operation date, period, status, captured. */
export function CapturesTable({ label, rows, loading = false, empty, showTransaction = true }: CapturesTableProps) {
  const { t } = useTranslation()
  const columns: Column<ReportableEventSummary>[] = [
    { id: 'capture', header: t('captures.events.table.capture'), cell: (row) => <span className="font-mono text-xs">{row.capture_code}</span> },
    { id: 'report', header: t('captures.events.table.report'), cell: (row) => <span className="font-mono text-xs">{row.report_code}</span> },
    { id: 'kind', header: t('captures.events.table.kind'), cell: (row) => t(`captures.kind.${row.capture_kind}`) },
    ...(showTransaction
      ? [
          {
            id: 'transaction',
            header: t('captures.events.table.transaction'),
            cell: (row: ReportableEventSummary) => (
              <Link to={`/transactions/${row.transaction_id}`} className="font-mono text-xs underline-offset-4 hover:underline">
                {row.transaction_id}
              </Link>
            ),
          },
        ]
      : []),
    { id: 'occurredAt', header: t('captures.events.table.occurredAt'), cell: (row) => <MexicoCityTimeCell value={row.occurred_at} /> },
    { id: 'period', header: t('captures.events.table.period'), cell: (row) => <span className="font-mono text-xs">{row.period_key}</span> },
    {
      id: 'status',
      header: t('captures.events.table.status'),
      cell: (row) => (
        <div className="flex flex-wrap gap-1">
          <EventStatusBadge status={row.status} />
          {row.late && <LateBadge />}
        </div>
      ),
    },
    { id: 'capturedAt', header: t('captures.events.table.capturedAt'), cell: (row) => <MexicoCityTimeCell value={row.captured_at} /> },
  ]
  return <DataTable label={label} columns={columns} rows={rows} getRowId={(row) => row.id} loading={loading} empty={empty} />
}
