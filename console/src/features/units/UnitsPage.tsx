import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { ConversionRule, UnitsResponse, UnitValuesResponse } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { useUnitLookup, useUnits, useUnitValues } from './queries'

type Summary = UnitsResponse['items'][number]
type UnitValue = UnitValuesResponse['items'][number]

/**
 * The API's conversion rules. Typed as a `Record<ConversionRule, ...>` so the build fails
 * when the API adds a rule missing here.
 */
const RULE_ORDER: Record<ConversionRule, number> = {
  SAME_DAY: 0,
  PRIOR_BANKING_DAY: 1,
  LAST_DAY_PREV_MONTH: 2,
  PREV_MONTH_AVG: 3,
  SEMIANNUAL_SNAPSHOT: 4,
}
const RULES = (Object.keys(RULE_ORDER) as ConversionRule[]).sort((a, b) => RULE_ORDER[a] - RULE_ORDER[b])

/** Days of history a unit tab opens on. */
const HISTORY_DAYS = 90

/** A calendar date where the viewer is, `daysBack` days before today, as YYYY-MM-DD. */
function localDate(daysBack = 0): string {
  const now = new Date()
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack)
  const month = String(day.getMonth() + 1).padStart(2, '0')
  return `${day.getFullYear()}-${month}-${String(day.getDate()).padStart(2, '0')}`
}

function SummaryTable({ rows }: { rows: readonly Summary[] | undefined }) {
  const { t } = useTranslation()
  const columns: Column<Summary>[] = [
    { id: 'unit', header: t('units.table.unit'), cell: (row) => <span className="font-mono">{row.unit_code}</span> },
    { id: 'quoteCcy', header: t('units.table.quoteCcy'), cell: (row) => row.quote_ccy },
    { id: 'latest', header: t('units.table.latest'), cell: (row) => row.latest_valid_from ?? '—', className: 'tabular' },
    {
      id: 'fetchedAt',
      header: t('units.table.fetchedAt'),
      cell: (row) => (row.latest_fetched_at ? <DateTimeCell value={row.latest_fetched_at} /> : '—'),
    },
    {
      id: 'source',
      header: t('units.table.source'),
      cell: (row) => <span className="font-mono text-xs">{row.latest_source ?? '—'}</span>,
    },
  ]
  return (
    <DataTable
      label={t('units.table.label')}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.unit_code}
      empty="—"
    />
  )
}

function History({ code }: { code: string }) {
  const { t } = useTranslation()
  const [from, setFrom] = useState(() => localDate(HISTORY_DAYS))
  const [to, setTo] = useState(() => localDate())
  // Ask only for a complete range: a date input is empty while it is being edited.
  const [range, setRange] = useState({ from, to })
  const history = useUnitValues(code, range.from, range.to)

  function change(next: { from: string; to: string }) {
    setFrom(next.from)
    setTo(next.to)
    if (next.from !== '' && next.to !== '') setRange(next)
  }

  const columns: Column<UnitValue>[] = [
    { id: 'validFrom', header: t('units.history.validFrom'), cell: (row) => row.valid_from, className: 'tabular' },
    { id: 'validTo', header: t('units.history.validTo'), cell: (row) => row.valid_to ?? '—', className: 'tabular' },
    {
      id: 'value',
      header: t('units.history.value'),
      cell: (row) => (
        <span className="inline-flex items-center gap-2">
          <span className={cn('tabular', row.superseded_by !== null && 'text-muted-foreground line-through')}>
            {row.value} {row.quote_ccy}
          </span>
          {row.superseded_by !== null && <Badge variant="muted">{t('units.corrected')}</Badge>}
        </span>
      ),
    },
    {
      id: 'source',
      header: t('units.table.source'),
      cell: (row) => <span className="font-mono text-xs">{row.source}</span>,
    },
    { id: 'fetchedAt', header: t('units.history.fetchedAt'), cell: (row) => <DateTimeCell value={row.fetched_at} /> },
  ]

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('units.history.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-44">
            <Field label={t('units.history.from')}>
              {(control) => (
                <Input {...control} type="date" value={from} onChange={(event) => change({ from: event.target.value, to })} />
              )}
            </Field>
          </div>
          <div className="w-44">
            <Field label={t('units.history.to')}>
              {(control) => (
                <Input {...control} type="date" value={to} onChange={(event) => change({ from, to: event.target.value })} />
              )}
            </Field>
          </div>
        </div>
        {history.isError ? (
          <ErrorBlock error={history.error} onRetry={() => void history.refetch()} />
        ) : (
          <div className={cn(history.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <DataTable
              label={`${t('units.history.title')} ${code}`}
              columns={columns}
              rows={history.data?.items}
              getRowId={(row) => String(row.id)}
              loading={history.isPending}
              empty={t('units.history.empty')}
            />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Lookup({ code }: { code: string }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<{ on: string; rule: ConversionRule }>(() => ({ on: localDate(), rule: 'SAME_DAY' }))
  const [asked, setAsked] = useState<typeof draft | null>(null)
  const lookup = useUnitLookup(code, asked?.on ?? '', asked?.rule ?? 'SAME_DAY', asked !== null)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (draft.on !== '') setAsked(draft)
  }

  let result = null
  if (asked !== null) {
    if (lookup.isError) result = <ErrorBlock error={lookup.error} onRetry={() => void lookup.refetch()} />
    else if (lookup.data === undefined) result = <LoadingBlock rows={2} />
    else {
      const found = lookup.data
      result = (
        <div className="space-y-3">
          <p className="text-lg font-medium tabular">
            {t('units.lookup.result', { value: found.value, ccy: found.quote_ccy, date: found.resolved_date })}
          </p>
          <KeyValues
            items={[
              [t('units.lookup.rule'), <span key="rule" className="font-mono text-xs">{found.rule}</span>],
              [t('units.table.source'), <span key="source" className="font-mono text-xs">{found.source}</span>],
              [t('units.history.fetchedAt'), <DateTimeCell key="fetched" value={found.fetched_at} />],
            ]}
          />
        </div>
      )
    }
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('units.lookup.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={submit} noValidate className="flex flex-wrap items-end gap-3">
          <div className="w-44">
            <Field label={t('units.lookup.on')}>
              {(control) => (
                <Input
                  {...control}
                  type="date"
                  required
                  value={draft.on}
                  onChange={(event) => setDraft({ ...draft, on: event.target.value })}
                />
              )}
            </Field>
          </div>
          <div className="w-60">
            <Field label={t('units.lookup.rule')}>
              {(control) => (
                <Select value={draft.rule} onValueChange={(rule) => setDraft({ ...draft, rule: rule as ConversionRule })}>
                  <SelectTrigger {...control} className="w-full font-mono text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RULES.map((rule) => (
                      <SelectItem key={rule} value={rule} className="font-mono text-xs">
                        {rule}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
          </div>
          <Button type="submit" disabled={lookup.isFetching}>
            {t('units.lookup.submit')}
          </Button>
        </form>
        {result}
      </CardContent>
    </Card>
  )
}

/** The unit values: a summary per unit, then a tab per unit with its history and a lookup. */
export function UnitsPage() {
  const { t } = useTranslation()
  const units = useUnits()
  const items = units.data?.items

  return (
    <>
      <PageHeader title={t('units.title')} description={t('units.description')} />

      <div className="space-y-6">
        <Card className="animate-fade-in-up">
          <CardContent>
            {units.isError ? (
              <ErrorBlock error={units.error} onRetry={() => void units.refetch()} />
            ) : (
              <SummaryTable rows={items} />
            )}
          </CardContent>
        </Card>

        {items !== undefined && items.length > 0 && (
          <Tabs defaultValue={items[0]?.unit_code}>
            <TabsList>
              {items.map((unit) => (
                <TabsTrigger key={unit.unit_code} value={unit.unit_code} className="font-mono text-xs">
                  {unit.unit_code}
                </TabsTrigger>
              ))}
            </TabsList>
            {items.map((unit) => (
              <TabsContent key={unit.unit_code} value={unit.unit_code} className="space-y-6">
                <History code={unit.unit_code} />
                <Lookup code={unit.unit_code} />
              </TabsContent>
            ))}
          </Tabs>
        )}
      </div>
    </>
  )
}
