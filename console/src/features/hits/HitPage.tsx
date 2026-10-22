import { ArrowLeft } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { isApiError } from '@/api/errors'
import type { HitDetail, HitTransactionView } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { formatMinor } from '@/features/transactions/amount'
import { CounterpartyScreeningBadge } from '@/features/transactions/CounterpartyCard'
import { MexicoCityTimeCell } from '@/features/transactions/MexicoCityTimeCell'
import { mexicoCityDateTime } from '@/features/transactions/time'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { NotFound } from '@/NotFound'
import { EvidenceTable } from './EvidenceTable'
import { HitRevealCard } from './HitRevealCard'
import { MaskedPartyCell } from './HitsTable'
import { useHit, useHitTransactions } from './queries'
import { OutcomeBadge, SeverityBadge, TypologyTags } from './SeverityBadge'

const mono = (value: string) => <span className="font-mono text-xs break-all">{value}</span>

/** The rule as the hit's version renders it, and the text stored when it fired if that differs. */
function SentenceCard({ hit }: { hit: HitDetail }) {
  const { t } = useTranslation()
  const explanation = hit.evidence.explanation
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('hits.detail.sentence')}</CardTitle>
        <CardDescription>{mono(hit.rule_code)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-base">{hit.sentence}</p>
        {explanation !== '' && explanation !== hit.sentence && (
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">{t('hits.detail.explanation')}</p>
            <p className="text-sm">{explanation}</p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function SummaryCard({ hit }: { hit: HitDetail }) {
  const { t } = useTranslation()
  const { locale } = useFormat()
  const rows: [string, ReactNode][] = [
    [t('hits.table.party'), <MaskedPartyCell key="party" party={hit.party} />],
    [t('hits.table.typology'), <TypologyTags key="typology" typology={hit.typology} />],
    [t('hits.table.severity'), <SeverityBadge key="severity" severity={hit.severity} />],
    [t('hits.table.outcome'), <OutcomeBadge key="outcome" outcome={hit.outcome} />],
    [
      t('hits.table.window'),
      `${mexicoCityDateTime(hit.window_start, locale)} – ${mexicoCityDateTime(hit.window_end, locale)}`,
    ],
    [t('hits.table.firedCount'), <span key="count" className="tabular">{hit.fired_count}</span>],
    [
      t('hits.detail.firstOccurredAt'),
      <Link key="first" to={`/transactions/${hit.first_transaction_id}`} className="underline-offset-4 hover:underline">
        {mexicoCityDateTime(hit.first_occurred_at, locale)}
      </Link>,
    ],
    [t('hits.table.createdAt'), mexicoCityDateTime(hit.created_at, locale)],
  ]
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('hits.detail.summary')}</CardTitle>
        <CardDescription>{t('hits.detail.timeZone')}</CardDescription>
      </CardHeader>
      <CardContent>
        <KeyValues items={rows} />
      </CardContent>
    </Card>
  )
}

/** The dedup key, whether a cooldown is running, the decision (4c) and what the hit was judged under. */
function StateCard({ hit }: { hit: HitDetail }) {
  const { t } = useTranslation()
  const { locale } = useFormat()
  // When the page opened: the cooldown reads as running or ended against it.
  const [openedAt] = useState(() => Date.now())
  let cooldown = t('hits.detail.cooldownNone')
  if (hit.cooldown_until !== null) {
    const date = mexicoCityDateTime(hit.cooldown_until, locale)
    cooldown =
      Date.parse(hit.cooldown_until) > openedAt
        ? t('hits.detail.cooldownActive', { date })
        : t('hits.detail.cooldownEnded', { date })
  }
  // The decision route is 4c's; until the console has it, the id is shown as text.
  const decision = hit.decision_id === null ? t('hits.detail.undecided') : t('hits.detail.decided', { id: hit.decision_id })
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('hits.detail.state')}</CardTitle>
      </CardHeader>
      <CardContent>
        <KeyValues
          items={[
            [t('hits.detail.dedup'), mono(hit.dedup_key)],
            [t('hits.detail.cooldown'), cooldown],
            [t('hits.detail.decision'), decision],
            [t('hits.detail.definitionHash'), mono(hit.definition_hash)],
            [t('hits.detail.snapshot'), mono(hit.policy_snapshot_id)],
            [t('hits.detail.listsHash'), mono(hit.evidence.lists_hash)],
          ]}
        />
      </CardContent>
    </Card>
  )
}

function useTransactionColumns(): Column<HitTransactionView>[] {
  const { t } = useTranslation()
  const { locale } = useFormat()
  return [
    { id: 'role', header: t('hits.transactions.role'), cell: (row) => t(`hits.role.${row.role}`) },
    {
      id: 'occurredAt',
      header: t('hits.transactions.occurredAt'),
      cell: (row) => (
        <Link to={`/transactions/${row.transaction_id}`} className="underline-offset-4 hover:underline">
          <MexicoCityTimeCell value={row.occurred_at} />
        </Link>
      ),
    },
    { id: 'direction', header: t('hits.transactions.direction'), cell: (row) => t(`transactions.direction.${row.direction}`) },
    {
      id: 'instrumentRail',
      header: t('hits.transactions.instrumentRail'),
      cell: (row) => `${t(`transactions.instrument.${row.instrument}`)} · ${t(`transactions.rail.${row.rail}`)}`,
    },
    {
      id: 'amount',
      header: t('hits.transactions.amount'),
      cell: (row) => <span className="tabular">{formatMinor(row.amount_minor, row.currency, locale)}</span>,
      className: 'text-right',
    },
    {
      id: 'counterparty',
      header: t('hits.transactions.counterparty'),
      cell: (row) =>
        row.counterparty === null ? (
          <span className="text-muted-foreground">{t('hits.transactions.noCounterparty')}</span>
        ) : (
          <div className="space-y-1">
            <p>
              {t(`transactions.counterpartyKind.${row.counterparty.kind}`)}
              {row.counterparty.institution === null ? '' : ` · ${row.counterparty.institution}`}
              {row.counterparty.country === null ? '' : ` · ${row.counterparty.country}`}
            </p>
            <CounterpartyScreeningBadge status={row.counterparty.screening_status} />
          </div>
        ),
    },
  ]
}

/** Every transaction linked to the hit, a page at a time, when the detail holds only the newest. */
function PagedTransactions({ hitId }: { hitId: string }) {
  const { t } = useTranslation()
  const columns = useTransactionColumns()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const page = useHitTransactions(hitId, cursor)
  if (page.isError) return <ErrorBlock error={page.error} onRetry={() => void page.refetch()} />
  return (
    <div className="space-y-4">
      <div className={cn(page.isPlaceholderData && 'opacity-60 transition-opacity')}>
        <DataTable
          label={t('hits.detail.transactionsLabel')}
          columns={columns}
          rows={page.data?.items}
          getRowId={(row) => row.transaction_id}
          loading={page.isPending}
          empty={t('hits.detail.transactionsEmpty')}
        />
      </div>
      <CursorPager cursor={cursor} nextCursor={page.data?.next_cursor} onChange={setCursor} busy={page.isPlaceholderData} />
    </div>
  )
}

function TransactionsCard({ hit }: { hit: HitDetail }) {
  const { t } = useTranslation()
  const columns = useTransactionColumns()
  const partial = hit.transaction_count > hit.transactions.length
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('hits.detail.transactions')}</CardTitle>
        <CardDescription>{t('hits.detail.transactionsTotal', { count: hit.transaction_count })}</CardDescription>
      </CardHeader>
      <CardContent>
        {partial ? (
          <PagedTransactions hitId={hit.id} />
        ) : (
          <DataTable
            label={t('hits.detail.transactionsLabel')}
            columns={columns}
            rows={hit.transactions}
            getRowId={(row) => row.transaction_id}
            empty={t('hits.detail.transactionsEmpty')}
          />
        )}
      </CardContent>
    </Card>
  )
}

/**
 * One hit: the rule's sentence, the summary, the values it read against its parameters, the
 * evidence transactions (masked), the dedup and cooldown state and the decision, and the audited
 * reveal. There is no disposition and no note (spec section 17).
 */
function HitDetailView({ hitId }: { hitId: string }) {
  const { t } = useTranslation()
  const hit = useHit(hitId)

  if (isApiError(hit.error) && hit.error.status === 404) return <NotFound />

  let body: ReactNode
  if (hit.isError) body = <ErrorBlock error={hit.error} onRetry={() => void hit.refetch()} />
  else if (hit.data === undefined) body = <LoadingBlock rows={6} />
  else {
    const data = hit.data
    body = (
      <div className="space-y-6">
        <SentenceCard hit={data} />
        <div className="grid gap-6 lg:grid-cols-2">
          <SummaryCard hit={data} />
          <div className="space-y-6">
            <StateCard hit={data} />
            <HitRevealCard hitId={data.id} transactions={data.transactions} />
          </div>
        </div>
        <Card className="animate-fade-in-up">
          <CardContent className="pt-6">
            <EvidenceTable evidence={data.evidence} />
          </CardContent>
        </Card>
        <TransactionsCard hit={data} />
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={t('hits.detail.title')}
        actions={
          <Button asChild variant="outline">
            <Link to="/monitoring/hits">
              <ArrowLeft aria-hidden />
              {t('hits.detail.back')}
            </Link>
          </Button>
        }
      />
      {body}
    </>
  )
}

export function HitPage() {
  const { hitId } = useParams()
  if (hitId === undefined || hitId === '') return <NotFound />
  // Keyed by the hit, so a reveal never outlives the page it was asked on.
  return <HitDetailView key={hitId} hitId={hitId} />
}
