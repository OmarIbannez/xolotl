import { Pencil, TriangleAlert, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { BehaviourProfileResponse, CustomerRelationshipResponse, Instrument } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DataTable, type Column } from '@/components/common/DataTable'
import { KeyValues } from '@/components/common/page'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import type { Locale } from '@/i18n'
import { useLocale } from '@/i18n/use-locale'
import { DeclaredProfileDialog } from './DeclaredProfileDialog'
import {
  formatMxnMinor,
  formatRatio,
  formatShare,
  INSTRUMENTS,
  readDeclared,
  readDeviation,
  readMetrics,
  type DirectionDeviation,
} from './profile-form'
import { useBehaviourProfiles, useSetDeclaredProfile } from './queries'

const monthFormats = new Map<string, Intl.DateTimeFormat>()

/** "septiembre de 2026" (`long`) or "sept 26" (`short`) for a "YYYY-MM" month. */
function monthLabel(month: string, locale: Locale, style: 'long' | 'short'): string {
  const key = `${locale}|${style}`
  let format = monthFormats.get(key)
  if (format === undefined) {
    format = new Intl.DateTimeFormat(locale, {
      month: style,
      year: style === 'long' ? 'numeric' : '2-digit',
      timeZone: 'UTC',
    })
    monthFormats.set(key, format)
  }
  const [year = 1970, number = 1] = month.split('-').map(Number)
  return format.format(new Date(Date.UTC(year, number - 1, 1)))
}

const isInstrument = (code: string): code is Instrument => (INSTRUMENTS as readonly string[]).includes(code)

// The chart's geometry, in SVG user units.
const GROUP = 44
const BAR = 14
const HEIGHT = 140
const AXIS = 18

/**
 * Inflow and outflow per month (oldest left), with each month's declared amounts as dashed lines.
 * Pixel heights are the only place amounts become floats; every number shown is formatted from the
 * API's centavo strings.
 */
function ProfileChart({ rows }: { rows: readonly BehaviourProfileResponse[] }) {
  const { t } = useTranslation()
  const locale = useLocale()
  const months = rows.toReversed().map((row) => ({
    month: row.month,
    metrics: readMetrics(row.metrics),
    declared: readDeclared(row.declared_profile),
  }))
  const values = months.flatMap(({ metrics, declared }) => [
    Number(metrics.inflowMinor),
    Number(metrics.outflowMinor),
    Number(declared?.inflowMinor ?? 0n),
    Number(declared?.outflowMinor ?? 0n),
  ])
  const max = Math.max(1, ...values)
  const y = (minor: number) => HEIGHT - (minor / max) * HEIGHT
  const anyDeclared = months.some(({ declared }) => declared !== null)
  const first = months[0]?.month ?? ''
  const last = months.at(-1)?.month ?? ''

  return (
    <figure className="space-y-2">
      <svg
        role="img"
        aria-label={t('parties.profile.chart.label', {
          from: monthLabel(first, locale, 'long'),
          to: monthLabel(last, locale, 'long'),
        })}
        viewBox={`0 0 ${months.length * GROUP} ${HEIGHT + AXIS}`}
        className="h-48 w-full max-w-3xl"
        preserveAspectRatio="xMinYMid meet"
      >
        <line x1={0} x2={months.length * GROUP} y1={HEIGHT} y2={HEIGHT} className="stroke-border" />
        {months.map(({ month, metrics, declared }, index) => {
          const left = index * GROUP + (GROUP - 2 * BAR) / 2
          const inflow = Number(metrics.inflowMinor)
          const outflow = Number(metrics.outflowMinor)
          return (
            <g key={month}>
              <rect data-bar="inflow" x={left} y={y(inflow)} width={BAR} height={HEIGHT - y(inflow)} className="fill-primary" />
              <rect
                data-bar="outflow"
                x={left + BAR}
                y={y(outflow)}
                width={BAR}
                height={HEIGHT - y(outflow)}
                className="fill-muted-foreground/60"
              />
              {declared?.inflowMinor != null && (
                <line
                  data-declared="inflow"
                  x1={left - 2}
                  x2={left + BAR + 1}
                  y1={y(Number(declared.inflowMinor))}
                  y2={y(Number(declared.inflowMinor))}
                  strokeDasharray="3 2"
                  strokeWidth={1.5}
                  className="stroke-foreground"
                />
              )}
              {declared?.outflowMinor != null && (
                <line
                  data-declared="outflow"
                  x1={left + BAR - 1}
                  x2={left + 2 * BAR + 2}
                  y1={y(Number(declared.outflowMinor))}
                  y2={y(Number(declared.outflowMinor))}
                  strokeDasharray="3 2"
                  strokeWidth={1.5}
                  className="stroke-foreground"
                />
              )}
              <text x={index * GROUP + GROUP / 2} y={HEIGHT + 13} textAnchor="middle" className="fill-muted-foreground text-[9px]">
                {monthLabel(month, locale, 'short')}
              </text>
            </g>
          )
        })}
      </svg>
      <figcaption className="flex flex-wrap gap-4 text-xs text-muted-foreground" aria-hidden>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-primary" />
          {t('parties.profile.chart.inflow')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-muted-foreground/60" />
          {t('parties.profile.chart.outflow')}
        </span>
        {anyDeclared && (
          <span className="inline-flex items-center gap-1.5">
            <span className="w-4 border-t-2 border-dashed border-foreground" />
            {t('parties.profile.chart.declared')}
          </span>
        )}
      </figcaption>
    </figure>
  )
}

/** The month's comparison with its declaration, one line per direction that has one. */
function DeviationCell({ row }: { row: BehaviourProfileResponse }) {
  const { t } = useTranslation()
  const locale = useLocale()
  const deviation = readDeviation(row.deviation)
  if (deviation === null) return <span className="text-muted-foreground">{t('parties.profile.deviation.none')}</span>
  const line = (direction: DirectionDeviation | null, side: 'inflow' | 'outflow') => {
    if (direction === null) return null
    if (direction.kind === 'declaredZero') return <li key={side}>{t(`parties.profile.deviation.${side}DeclaredZero`)}</li>
    return <li key={side}>{t(`parties.profile.deviation.${side}Ratio`, { ratio: formatRatio(direction.ratio, locale) })}</li>
  }
  return (
    <div className="space-y-1">
      {row.deviated && (
        <Badge variant="warning">
          <TriangleAlert aria-hidden />
          {t('parties.profile.deviated')}
        </Badge>
      )}
      <ul className="text-xs">
        {line(deviation.inflow, 'inflow')}
        {line(deviation.outflow, 'outflow')}
      </ul>
    </div>
  )
}

/** The relationship's current declaration, as the dialog would prefill it. */
function DeclaredSummary({ relationship }: { relationship: CustomerRelationshipResponse }) {
  const { t } = useTranslation()
  const locale = useLocale()
  const declared = readDeclared(relationship.declared_profile)
  if (declared === null) return <p className="text-sm text-muted-foreground">{t('parties.profile.declared.none')}</p>
  const amount = (minor: bigint | null) => (minor === null ? '—' : formatMxnMinor(minor, locale))
  const list = (codes: readonly string[]) => (codes.length === 0 ? t('parties.profile.declared.noneListed') : codes.join(', '))
  return (
    <KeyValues
      items={[
        [t('parties.profile.declared.inflow'), <span key="in" className="tabular-nums">{amount(declared.inflowMinor)}</span>],
        [t('parties.profile.declared.outflow'), <span key="out" className="tabular-nums">{amount(declared.outflowMinor)}</span>],
        [t('parties.profile.declared.countries'), <span key="countries" className="font-mono">{list(declared.countries)}</span>],
        [
          t('parties.profile.declared.instruments'),
          list(declared.instruments.map((code) => (isInstrument(code) ? t(`transactions.instrument.${code}`) : code))),
        ],
      ]}
    />
  )
}

/**
 * A customer relationship's behaviour over its last 12 computed months: inflow and outflow against
 * the declared profile, the month's metrics, and the deviation flag the API computed. Writers set
 * or remove the declaration here. Staff only; nothing on it reaches a client.
 */
export function BehaviourProfileCard({ relationship }: { relationship: CustomerRelationshipResponse }) {
  const { t } = useTranslation()
  const locale = useLocale()
  const { hasRole } = useAuth()
  const canWrite = hasRole(ACCESS.partiesWrite)
  const profiles = useBehaviourProfiles(relationship.id)
  const clear = useSetDeclaredProfile(relationship.id)
  const [editing, setEditing] = useState(false)
  const [clearing, setClearing] = useState(false)
  const hasDeclaration = relationship.declared_profile !== null && relationship.declared_profile !== undefined

  function cancelClear() {
    clear.reset()
    setClearing(false)
  }

  const share = (value: string) => <span className="tabular-nums">{formatShare(value, locale)}</span>
  const columns: Column<BehaviourProfileResponse>[] = [
    { id: 'month', header: t('parties.profile.table.month'), cell: (row) => monthLabel(row.month, locale, 'long') },
    {
      id: 'inflow',
      header: t('parties.profile.table.inflow'),
      cell: (row) => <span className="tabular-nums">{formatMxnMinor(readMetrics(row.metrics).inflowMinor, locale)}</span>,
      className: 'text-right',
    },
    {
      id: 'outflow',
      header: t('parties.profile.table.outflow'),
      cell: (row) => <span className="tabular-nums">{formatMxnMinor(readMetrics(row.metrics).outflowMinor, locale)}</span>,
      className: 'text-right',
    },
    {
      id: 'operations',
      header: t('parties.profile.table.operations'),
      cell: (row) => {
        const metrics = readMetrics(row.metrics)
        return <span className="tabular-nums">{`${metrics.inflowCount} / ${metrics.outflowCount}`}</span>
      },
      className: 'text-right',
    },
    {
      id: 'counterparties',
      header: t('parties.profile.table.counterparties'),
      cell: (row) => <span className="tabular-nums">{readMetrics(row.metrics).distinctCounterparties}</span>,
      className: 'text-right',
    },
    {
      id: 'activeDays',
      header: t('parties.profile.table.activeDays'),
      cell: (row) => <span className="tabular-nums">{readMetrics(row.metrics).activeDays}</span>,
      className: 'text-right',
    },
    { id: 'cash', header: t('parties.profile.table.cash'), cell: (row) => share(readMetrics(row.metrics).cashShare), className: 'text-right' },
    {
      id: 'international',
      header: t('parties.profile.table.international'),
      cell: (row) => share(readMetrics(row.metrics).internationalShare),
      className: 'text-right',
    },
    { id: 'crypto', header: t('parties.profile.table.crypto'), cell: (row) => share(readMetrics(row.metrics).cryptoShare), className: 'text-right' },
    { id: 'deviation', header: t('parties.profile.table.deviation'), cell: (row) => <DeviationCell row={row} /> },
  ]

  let body
  if (profiles.isError) body = <ErrorBlock error={profiles.error} onRetry={() => void profiles.refetch()} />
  else if (profiles.data === undefined) body = <LoadingBlock rows={4} />
  else if (profiles.data.length === 0) {
    body = (
      <EmptyBlock>
        <span className="font-medium text-foreground">{t('parties.profile.empty')}</span>
        <span>{t('parties.profile.emptyHint')}</span>
      </EmptyBlock>
    )
  } else {
    body = (
      <>
        <ProfileChart rows={profiles.data} />
        <DataTable
          label={t('parties.profile.table.label')}
          columns={columns}
          rows={profiles.data}
          getRowId={(row) => row.month}
          empty={t('parties.profile.empty')}
        />
      </>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <CardTitle>{t('parties.profile.title', { ref: relationship.external_ref })}</CardTitle>
          <CardDescription>{t('parties.profile.description')}</CardDescription>
        </div>
        {canWrite && (
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil aria-hidden />
              {t('parties.profile.edit')}
            </Button>
            {hasDeclaration && (
              <Button type="button" variant="outline" size="sm" onClick={() => setClearing(true)}>
                <X aria-hidden />
                {t('parties.profile.clear')}
              </Button>
            )}
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="space-y-2">
          <h3 className="text-sm font-medium">{t('parties.profile.declared.title')}</h3>
          <DeclaredSummary relationship={relationship} />
        </section>
        {body}
      </CardContent>
      {editing && <DeclaredProfileDialog relationship={relationship} onClose={() => setEditing(false)} />}
      {clearing && (
        <ConfirmDialog
          title={t('parties.profile.clear')}
          description={t('parties.profile.clearConfirm')}
          confirmLabel={t('parties.profile.clear')}
          pendingLabel={t('parties.profile.clearing')}
          pending={clear.isPending}
          alert={clear.isError ? <FormAlert error={clear.error} /> : undefined}
          onConfirm={() =>
            clear.mutate(
              { declared_profile: null },
              {
                onSuccess: () => {
                  toast.success(t('parties.profile.cleared'))
                  setClearing(false)
                },
              },
            )
          }
          onCancel={cancelClear}
        />
      )}
    </Card>
  )
}
