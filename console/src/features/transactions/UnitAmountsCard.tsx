import { useTranslation } from 'react-i18next'
import type { UnitAmountView } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

/**
 * The transaction's amount in every threshold unit of its policy: one row per unit and
 * conversion rule, with the `unit_value` row used (none for the peso amount itself) and the
 * date that value is for.
 */
export function UnitAmountsCard({ amounts }: { amounts: readonly UnitAmountView[] }) {
  const { t } = useTranslation()
  const title = t('transactions.detail.units.title')
  const columns: Column<UnitAmountView>[] = [
    { id: 'unit', header: t('transactions.detail.units.unit'), cell: (row) => <span className="font-mono text-xs">{row.unit}</span> },
    { id: 'rule', header: t('transactions.detail.units.rule'), cell: (row) => <span className="font-mono text-xs">{row.rule}</span> },
    {
      id: 'amount',
      header: t('transactions.detail.units.amount'),
      cell: (row) => <span className="tabular font-mono text-xs">{row.amount}</span>,
      className: 'text-right',
    },
    {
      id: 'unitValue',
      header: t('transactions.detail.units.unitValue'),
      cell: (row) => <span className="tabular font-mono text-xs">{row.unit_value_id ?? '—'}</span>,
    },
    {
      id: 'resolvedDate',
      header: t('transactions.detail.units.resolvedDate'),
      cell: (row) => <time dateTime={row.resolved_date} className="font-mono text-xs">{row.resolved_date}</time>,
    },
  ]
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{t('transactions.detail.units.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          label={t('transactions.detail.units.label')}
          columns={columns}
          rows={amounts}
          getRowId={(row) => row.key}
          empty={t('transactions.detail.units.empty')}
        />
      </CardContent>
    </Card>
  )
}
