import { useTranslation } from 'react-i18next'
import type { EvidenceAggregate, EvidenceView } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'

type AggregateRow = EvidenceAggregate & { name: string }
type ParamRow = { name: string; value: string }

const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name)
const mono = (value: string) => <span className="font-mono text-xs">{value}</span>

/**
 * A hit's evidence: the aggregates the rule read when it fired (name, value, unit, window) beside
 * the parameters it ran with. The values are decimal strings and are shown exactly as the API
 * sent them. The detail carries no condition, so which parameter an aggregate was compared with
 * is read from the rendered sentence above, not paired here.
 */
export function EvidenceTable({ evidence }: { evidence: EvidenceView }) {
  const { t } = useTranslation()
  const aggregates: AggregateRow[] = Object.entries(evidence.aggregates)
    .map(([name, aggregate]) => ({ name, ...aggregate }))
    .sort(byName)
  const params: ParamRow[] = Object.entries(evidence.params)
    .map(([name, value]) => ({ name, value }))
    .sort(byName)

  const aggregateColumns: Column<AggregateRow>[] = [
    { id: 'name', header: t('hits.evidence.name'), cell: (row) => mono(row.name) },
    { id: 'value', header: t('hits.evidence.value'), cell: (row) => <span className="tabular">{row.value}</span>, className: 'text-right' },
    { id: 'unit', header: t('hits.evidence.unit'), cell: (row) => row.unit ?? '—' },
    { id: 'window', header: t('hits.evidence.window'), cell: (row) => (row.window === null ? '—' : mono(row.window)) },
  ]
  const paramColumns: Column<ParamRow>[] = [
    { id: 'name', header: t('hits.evidence.name'), cell: (row) => mono(row.name) },
    { id: 'value', header: t('hits.evidence.value'), cell: (row) => <span className="tabular">{row.value}</span>, className: 'text-right' },
  ]

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('hits.detail.aggregates')}</h3>
        <DataTable
          label={t('hits.detail.aggregates')}
          columns={aggregateColumns}
          rows={aggregates}
          getRowId={(row) => row.name}
          empty={t('hits.evidence.noAggregates')}
        />
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('hits.detail.params')}</h3>
        <DataTable
          label={t('hits.detail.params')}
          columns={paramColumns}
          rows={params}
          getRowId={(row) => row.name}
          empty={t('hits.evidence.noParams')}
        />
      </section>
    </div>
  )
}
