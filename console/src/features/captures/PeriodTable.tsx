import { CircleCheck, Clock, RotateCcw, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { CapturePeriodResponse, CapturePeriodStatus } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

const STATUS: Record<CapturePeriodStatus, { variant: 'info' | 'good' | 'warning'; icon: LucideIcon }> = {
  OPEN: { variant: 'info', icon: Clock },
  CLOSED: { variant: 'good', icon: CircleCheck },
  REOPENED: { variant: 'warning', icon: RotateCcw },
}

/** A period's status, with an icon and a label (never color alone). */
export function PeriodStatusBadge({ status }: { status: CapturePeriodStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`captures.status.${status}`)}
    </Badge>
  )
}

/**
 * Whether a person may close the period now: it is not closed yet and its last day is before
 * `today` (a Mexico City day), which is when the API accepts the close.
 */
function closable(period: CapturePeriodResponse, today: string): boolean {
  return period.status !== 'CLOSED' && period.period_end < today
}

function IdList({ label, ids }: { label: string; ids: readonly string[] }) {
  if (ids.length === 0) return null
  return (
    <div className="space-y-0.5">
      <p className="text-xs font-medium">{label}</p>
      <ul className="space-y-0.5">
        {ids.map((id) => (
          <li key={id}>
            <Link to={`/transactions/${id}`} className="font-mono text-xs underline-offset-4 hover:underline">
              {id}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The count of the close's discrepancies, with the transactions behind a disclosure. */
function Discrepancies({ period }: { period: CapturePeriodResponse }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (period.discrepancies === null) {
    return (
      <span className="text-muted-foreground">
        {period.reconciled_at === null ? t('captures.discrepancies.pending') : t('captures.discrepancies.none')}
      </span>
    )
  }
  const missing = period.discrepancies.missing ?? []
  const extra = period.discrepancies.extra ?? []
  const count = missing.length + extra.length
  if (count === 0) return <span className="text-muted-foreground">{t('captures.discrepancies.none')}</span>
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="space-y-1">
      <CollapsibleTrigger asChild>
        <Button type="button" variant="link" size="sm" className="h-auto p-0">
          {open ? t('captures.discrepancies.hide') : t('captures.discrepancies.show', { count })}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-2">
        <IdList label={t('captures.discrepancies.missing')} ids={missing} />
        <IdList label={t('captures.discrepancies.extra')} ids={extra} />
      </CollapsibleContent>
    </Collapsible>
  )
}

interface PeriodTableProps {
  rows: readonly CapturePeriodResponse[] | undefined
  loading: boolean
  /** Today as a Mexico City day (`YYYY-MM-DD`): a period closes by hand only once it has ended. */
  today: string
  /** Whether the user may close a period (`ACCESS.capturesClose`). */
  canClose: boolean
  onClose: (period: CapturePeriodResponse) => void
  onShowEvents: (period: CapturePeriodResponse) => void
}

/**
 * The capture periods: capture, period, status, live events, the events the close reconciled
 * and its discrepancies. A closer is offered "Cerrar periodo" on ended periods not closed yet.
 */
export function PeriodTable({ rows, loading, today, canClose, onClose, onShowEvents }: PeriodTableProps) {
  const { t } = useTranslation()
  const columns: Column<CapturePeriodResponse>[] = [
    {
      id: 'capture',
      header: t('captures.table.capture'),
      cell: (row) => (
        <div className="space-y-0.5">
          <span className="block font-mono text-xs">{row.capture_code}</span>
          <span className="block font-mono text-xs text-muted-foreground">{row.report_code}</span>
        </div>
      ),
    },
    {
      id: 'period',
      header: t('captures.table.period'),
      cell: (row) => (
        <div className="space-y-0.5">
          <span className="block font-mono text-xs">{row.period_key}</span>
          <span className="block text-xs whitespace-nowrap text-muted-foreground">
            {t('captures.period.range', { start: row.period_start, end: row.period_end })}
          </span>
        </div>
      ),
    },
    { id: 'status', header: t('captures.table.status'), cell: (row) => <PeriodStatusBadge status={row.status} /> },
    {
      id: 'events',
      header: t('captures.table.events'),
      cell: (row) => <span className="tabular">{row.event_count}</span>,
      className: 'text-right',
    },
    {
      id: 'reconciled',
      header: t('captures.table.reconciled'),
      cell: (row) => <span className="tabular">{row.reconciled_count ?? '—'}</span>,
      className: 'text-right',
    },
    { id: 'discrepancies', header: t('captures.table.discrepancies'), cell: (row) => <Discrepancies period={row} /> },
    {
      id: 'actions',
      header: <span className="sr-only">{t('captures.table.actions')}</span>,
      cell: (row) => (
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={() => onShowEvents(row)}>
            {t('captures.events.show')}
          </Button>
          {canClose && closable(row, today) && (
            <Button type="button" size="sm" variant="outline" onClick={() => onClose(row)}>
              {t('captures.close.action')}
            </Button>
          )}
        </div>
      ),
    },
  ]
  return (
    <DataTable
      label={t('captures.period.label')}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      loading={loading}
      empty={t('captures.period.empty')}
    />
  )
}
