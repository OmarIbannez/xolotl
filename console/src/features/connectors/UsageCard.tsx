import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ConnectorUsageRow } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { dayAsLocalDate, formatMicros, usageWindow, type UsageRange } from './format'
import { useConnectorUsage } from './queries'

const RANGES: readonly UsageRange[] = [7, 30]

/** Calls that did not get an answer from the remote: errors, timeouts and refusals by the breaker. */
function failedCalls(row: ConnectorUsageRow): number {
  return (row.outcomes.ERROR ?? 0) + (row.outcomes.TIMEOUT ?? 0) + (row.outcomes.CIRCUIT_OPEN ?? 0)
}

/** Calls and cost per connector and UTC day over the last 7 or 30 days, as a table with a meter per row. */
export function UsageCard() {
  const { t } = useTranslation()
  const format = useFormat()
  const [range, setRange] = useState<UsageRange>(7)
  const window = useMemo(() => usageWindow(range, new Date()), [range])
  const usage = useConnectorUsage(window)

  const rows = usage.data?.items
  const maxCalls = Math.max(1, ...(rows ?? []).map((row) => row.calls))

  const columns: Column<ConnectorUsageRow>[] = [
    {
      id: 'day',
      header: t('connectors.usage.day'),
      cell: (row) => format.shortDate(dayAsLocalDate(row.day)),
    },
    {
      id: 'connector',
      header: t('connectors.usage.connector'),
      cell: (row) => <span className="font-mono text-xs">{row.connector}</span>,
    },
    {
      id: 'calls',
      header: t('connectors.usage.calls'),
      cell: (row) => (
        <span className="flex items-center gap-2">
          <meter
            className="h-2 w-24"
            min={0}
            max={maxCalls}
            value={row.calls}
            aria-label={`${t('connectors.usage.calls')} · ${row.connector} · ${row.day}`}
          />
          <span className="tabular-nums">{format.number(row.calls)}</span>
        </span>
      ),
    },
    {
      id: 'failed',
      header: t('connectors.usage.failed'),
      cell: (row) => <span className="tabular-nums">{format.number(failedCalls(row))}</span>,
      className: 'text-right',
    },
    {
      id: 'cost',
      header: t('connectors.usage.cost'),
      cell: (row) => <span className="tabular-nums">{formatMicros(row.cost_micros, format.locale)}</span>,
      className: 'text-right',
    },
  ]

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle role="heading" aria-level={2}>
          {t('connectors.usage.title')}
        </CardTitle>
        <CardDescription>{t('connectors.usage.description')}</CardDescription>
        <CardAction>
          <div className="flex gap-1">
            {RANGES.map((days) => (
              <Button
                key={days}
                type="button"
                size="sm"
                variant={range === days ? 'default' : 'outline'}
                aria-pressed={range === days}
                onClick={() => setRange(days)}
              >
                {t(days === 7 ? 'connectors.usage.range7' : 'connectors.usage.range30')}
              </Button>
            ))}
          </div>
        </CardAction>
      </CardHeader>
      <CardContent>
        {usage.isError ? (
          <ErrorBlock error={usage.error} onRetry={() => void usage.refetch()} />
        ) : (
          <div className={cn(usage.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <DataTable
              label={t('connectors.usage.label')}
              columns={columns}
              rows={rows}
              getRowId={(row) => `${row.day}|${row.connector}`}
              loading={usage.isPending}
              empty={t('connectors.usage.empty')}
            />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
