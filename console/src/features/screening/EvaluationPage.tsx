import { CircleAlert, CircleCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { hasProblemCode } from '@/api/errors'
import type { EvaluationFloor, EvaluationKind } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { KeyValues, PageHeader } from '@/components/common/page'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { useEvaluation } from './queries'

const KINDS = ['SANCTION', 'FISCAL', 'ALL'] as const
const METRICS = ['recall', 'retrieval_recall', 'precision_at_alert', 'alerts_per_1000_negatives'] as const

const isKnown = <T extends string>(known: readonly T[], value: string): value is T =>
  (known as readonly string[]).includes(value)

/**
 * The matcher's evaluation report shipped with the build (spec §14, FR-SC-014): recall, precision
 * at ALERT and retrieval recall per list kind, and each floor with whether the build meets it.
 * Ratios are shown as the report states them (0 to 1, four places).
 */
export function EvaluationPage() {
  const { t } = useTranslation()
  const format = useFormat()
  const evaluation = useEvaluation()
  const kindLabel = (kind: string) => (isKnown(KINDS, kind) ? t(`screening.evaluation.kind.${kind}`) : kind)
  const metricLabel = (metric: string) => (isKnown(METRICS, metric) ? t(`screening.evaluation.metric.${metric}`) : metric)
  const ratio = (value: string) => <span className="tabular">{value}</span>

  const kindColumns: Column<EvaluationKind>[] = [
    { id: 'kind', header: t('screening.evaluation.kinds.kind'), cell: (row) => kindLabel(row.list_kind) },
    {
      id: 'pairs',
      header: t('screening.evaluation.kinds.pairs'),
      cell: (row) => <span className="tabular">{format.number(row.pairs)}</span>,
      className: 'text-right',
    },
    { id: 'recall', header: metricLabel('recall'), cell: (row) => ratio(row.recall_review), className: 'text-right' },
    {
      id: 'precision',
      header: metricLabel('precision_at_alert'),
      cell: (row) =>
        row.precision_alert === null ? <span className="text-muted-foreground">—</span> : ratio(row.precision_alert),
      className: 'text-right',
    },
    {
      id: 'retrieval',
      header: metricLabel('retrieval_recall'),
      cell: (row) => ratio(row.retrieval_recall),
      className: 'text-right',
    },
  ]

  const floorColumns: Column<EvaluationFloor>[] = [
    { id: 'kind', header: t('screening.evaluation.kinds.kind'), cell: (row) => kindLabel(row.list_kind) },
    { id: 'metric', header: t('screening.evaluation.floors.metric'), cell: (row) => metricLabel(row.metric) },
    { id: 'floor', header: t('screening.evaluation.floors.floor'), cell: (row) => ratio(row.floor), className: 'text-right' },
    { id: 'value', header: t('screening.evaluation.floors.value'), cell: (row) => ratio(row.value), className: 'text-right' },
    {
      id: 'met',
      header: t('screening.evaluation.floors.result'),
      cell: (row) =>
        row.met ? (
          <Badge variant="good">
            <CircleCheck aria-hidden />
            {t('screening.evaluation.floors.met')}
          </Badge>
        ) : (
          <Badge variant="critical">
            <CircleAlert aria-hidden />
            {t('screening.evaluation.floors.below')}
          </Badge>
        ),
    },
  ]

  function body() {
    if (evaluation.isPending) return <LoadingBlock />
    if (evaluation.isError) {
      return hasProblemCode(evaluation.error, 'NOT_FOUND') ? (
        <EmptyBlock>{t('screening.evaluation.empty')}</EmptyBlock>
      ) : (
        <ErrorBlock error={evaluation.error} onRetry={() => void evaluation.refetch()} />
      )
    }
    const report = evaluation.data
    return (
      <div className="space-y-4">
        <Card className="animate-fade-in-up">
          <CardContent>
            <KeyValues
              items={[
                [t('screening.evaluation.matcherVersion'), <span className="font-mono text-xs">{report.matcher_version}</span>],
                [
                  t('screening.evaluation.generatedAt'),
                  <time dateTime={report.generated_at}>{format.dateTime(report.generated_at)}</time>,
                ],
                [t('screening.evaluation.corpusPairs'), <span className="tabular">{format.number(report.corpus_pairs)}</span>],
                [t('screening.evaluation.hardNegatives'), <span className="tabular">{format.number(report.hard_negatives)}</span>],
                [metricLabel('alerts_per_1000_negatives'), ratio(report.alerts_per_1000_negatives)],
              ]}
            />
          </CardContent>
        </Card>
        <Card className="animate-fade-in-up">
          <CardHeader>
            <CardTitle>{t('screening.evaluation.kinds.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              label={t('screening.evaluation.kinds.title')}
              columns={kindColumns}
              rows={report.kinds}
              getRowId={(row) => row.list_kind}
              empty={t('screening.evaluation.kinds.empty')}
            />
          </CardContent>
        </Card>
        <Card className="animate-fade-in-up">
          <CardHeader>
            <CardTitle>{t('screening.evaluation.floors.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              label={t('screening.evaluation.floors.title')}
              columns={floorColumns}
              rows={report.floors}
              getRowId={(row) => `${row.list_kind}:${row.metric}`}
              empty={t('screening.evaluation.floors.empty')}
            />
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <>
      <PageHeader title={t('screening.evaluation.title')} description={t('screening.evaluation.description')} />
      {body()}
    </>
  )
}
