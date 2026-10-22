import { CircleAlert, LoaderCircle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { SimulationResponse, VersionResponse } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { KeyValues } from '@/components/common/page'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { mexicoCityToday } from '@/features/transactions/time'
import { useFormat } from '@/i18n/use-locale'
import { lastSixMonths } from './params'
import { SIMULATION_POLL_MS, simulationRunning, useSimulate, useSimulation } from './queries'
import { SimulationAgeBadge } from './RuleStatusBadge'

type Json = Readonly<Record<string, unknown>>

interface Week {
  week: string
  hits: number
  customers: number
}

interface SampleRow {
  key: string
  partyId: string
  transactionId: string
  dedupKey: string
}

const OVERLAP = ['existing_hits', 'same_customers', 'later_true_match'] as const
/** The notes the catalogue explains; another code is shown as it comes. */
const NOTES = ['PROFILE_HISTORY_SHORT'] as const
const isNote = (value: string): value is (typeof NOTES)[number] => (NOTES as readonly string[]).includes(value)

const text = (value: unknown) => (typeof value === 'string' ? value : '')
const count = (value: unknown) => (typeof value === 'number' && Number.isInteger(value) ? value : 0)

/** The simulation's weeks, read leniently (the OpenAPI document types them as free JSON). */
function weeksOf(simulation: SimulationResponse): Week[] {
  return (simulation.hits_per_week ?? []).map((item: Json) => ({
    week: text(item.week),
    hits: count(item.hits),
    customers: count(item.customers),
  }))
}

/** The sample's ids-only references: the party, the transaction that fired and the dedup key. */
function sampleOf(simulation: SimulationResponse): SampleRow[] {
  return (simulation.sample ?? []).map((item: Json, index) => ({
    key: `${index}`,
    partyId: text(item.party_id),
    transactionId: text(item.trigger_transaction_id),
    dedupKey: text(item.dedup_key),
  }))
}

const mono = (value: string) => <span className="font-mono text-xs break-all">{value}</span>

/** A completed simulation: its tallies, the hits per week, the overlap and the sample of ids. */
function SimulationResult({ simulation }: { simulation: SimulationResponse }) {
  const { t } = useTranslation()
  const format = useFormat()
  const day = (value: string) => format.date(`${value}T12:00:00Z`)
  const rows: [string, ReactNode][] = [
    [t('rules.simulation.range'), `${day(simulation.range_start)} – ${day(simulation.range_end)}`],
    [t('rules.simulation.transactionsRead'), <span key="read" className="tabular">{format.number(simulation.transactions_read ?? 0)}</span>],
    [t('rules.simulation.hits'), <span key="hits" className="tabular">{format.number(simulation.hit_count ?? 0)}</span>],
    [t('rules.simulation.customers'), <span key="customers" className="tabular">{format.number(simulation.customers_hit ?? 0)}</span>],
    [t('rules.simulation.age'), <SimulationAgeBadge key="age" simulation={simulation} />],
  ]
  const weekColumns: Column<Week>[] = [
    { id: 'week', header: t('rules.simulation.week'), cell: (row) => mono(row.week) },
    { id: 'hits', header: t('rules.simulation.hits'), cell: (row) => <span className="tabular">{row.hits}</span>, className: 'text-right' },
    {
      id: 'customers',
      header: t('rules.simulation.customers'),
      cell: (row) => <span className="tabular">{row.customers}</span>,
      className: 'text-right',
    },
  ]
  const sampleColumns: Column<SampleRow>[] = [
    {
      id: 'party',
      header: t('rules.simulation.sampleParty'),
      cell: (row) => (
        <Link to={`/parties/${row.partyId}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.partyId}
        </Link>
      ),
    },
    {
      id: 'transaction',
      header: t('rules.simulation.sampleTransaction'),
      cell: (row) => (
        <Link to={`/transactions/${row.transactionId}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.transactionId}
        </Link>
      ),
    },
    { id: 'dedup', header: t('rules.simulation.sampleDedup'), cell: (row) => mono(row.dedupKey) },
  ]
  const overlap = simulation.overlap ?? {}
  return (
    <div className="space-y-5">
      <KeyValues items={rows} />
      {simulation.notes.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-muted-foreground">
          {simulation.notes.map((note) => (
            <li key={note}>{isNote(note) ? t(`rules.simulation.notes.${note}`) : note}</li>
          ))}
        </ul>
      )}
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('rules.simulation.hitsPerWeek')}</h3>
        <DataTable
          label={t('rules.simulation.hitsPerWeek')}
          columns={weekColumns}
          rows={weeksOf(simulation)}
          getRowId={(row) => row.week}
          empty={t('rules.simulation.noHits')}
        />
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('rules.simulation.overlap.title')}</h3>
        <KeyValues
          items={OVERLAP.map((name) => [
            t(`rules.simulation.overlap.${name}`),
            <span key={name} className="tabular">
              {format.number(overlap[name] ?? 0)}
            </span>,
          ])}
        />
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('rules.simulation.sample')}</h3>
        <p className="text-xs text-muted-foreground">{t('rules.simulation.sampleHint')}</p>
        <DataTable
          label={t('rules.simulation.sample')}
          columns={sampleColumns}
          rows={sampleOf(simulation)}
          getRowId={(row) => row.key}
          empty={t('rules.simulation.noHits')}
        />
      </section>
    </div>
  )
}

interface SimulationPanelProps {
  ruleId: string
  version: VersionResponse
  /** How often a running simulation is read again (tests shorten it). */
  pollMs?: number
  /** The editor holds changes not saved yet: a simulation would replay the saved definition. */
  unsaved?: boolean
}

/**
 * Simulate a version over the last six months (Mexico City days, today included) and show the
 * version's latest simulation: progress while it runs, its failure, or its tallies with the age
 * badge from `valid_until`. The tallies and the sample say which customers the rule would hit,
 * so they live only as long as this page (the queries keep nothing once it closes).
 */
export function SimulationPanel({ ruleId, version, pollMs = SIMULATION_POLL_MS, unsaved = false }: SimulationPanelProps) {
  const { t } = useTranslation()
  const format = useFormat()
  const simulate = useSimulate(ruleId, version.version)
  const [started, setStarted] = useState<string | undefined>(undefined)
  // Read the simulation itself only while one runs: the one just asked for, or the version's own.
  const pollId = started ?? (simulationRunning(version.simulation?.status) ? version.simulation?.id : undefined)
  const read = useSimulation(pollId, { pollMs })
  const simulation = read.data ?? (started === undefined ? version.simulation : null)
  // A read that failed is not a simulation still running: its error is shown instead.
  const running = !read.isError && (simulationRunning(simulation?.status) || (started !== undefined && read.data === undefined))
  const allowed = version.allowed_actions.includes('SIMULATE')
  const range = lastSixMonths(mexicoCityToday())
  const day = (value: string) => format.date(`${value}T12:00:00Z`)

  function run() {
    simulate.mutate(range, { onSuccess: ({ data }) => setStarted(data.simulation_id) })
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('rules.simulation.title')}</CardTitle>
        <CardDescription>{t('rules.simulation.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" onClick={run} disabled={!allowed || unsaved || running || simulate.isPending}>
            {t('rules.simulation.run')}
          </Button>
          <span className="text-xs text-muted-foreground">
            {t('rules.simulation.runRange', { start: day(range.range_start), end: day(range.range_end) })}
          </span>
        </div>
        {allowed && unsaved && <p className="text-sm text-muted-foreground">{t('rules.simulation.saveFirst')}</p>}
        {simulate.isError && <FormAlert error={simulate.error} />}
        {read.isError && <FormAlert error={read.error} />}
        <div role="status" aria-live="polite">
          {running && (
            <p className="flex items-center gap-2 text-sm">
              <LoaderCircle aria-hidden className="size-4 animate-spin" />
              {t(simulation?.status === 'RUNNING' ? 'rules.simulation.running' : 'rules.simulation.queued')}
            </p>
          )}
        </div>
        {running && <p className="text-xs text-muted-foreground">{t('rules.simulation.stuck')}</p>}
        {simulation?.status === 'FAILED' && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden />
            <AlertDescription>
              {t('rules.simulation.failed')}
              {simulation.last_error !== null && (
                <span className="mt-0.5 block font-mono text-xs">{simulation.last_error}</span>
              )}
            </AlertDescription>
          </Alert>
        )}
        {simulation?.status === 'COMPLETED' && simulation.definition_hash !== version.definition_hash && (
          <p className="text-sm text-muted-foreground">{t('rules.simulation.otherDefinition')}</p>
        )}
        {simulation?.status === 'COMPLETED' && <SimulationResult simulation={simulation} />}
        {simulation === null && !running && <p className="text-sm text-muted-foreground">{t('rules.simulation.none')}</p>}
      </CardContent>
    </Card>
  )
}
