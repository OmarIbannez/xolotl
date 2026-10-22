import { ArrowLeft, Eye, EyeOff, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import type { CounterpartyDetail, RevealedCounterparty, RunResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BandBadge } from '@/features/screening/BandBadge'
import { MatchStatusBadge } from '@/features/screening/MatchStatusBadge'
import { SubjectStatusBadge } from '@/features/screening/SubjectStatusBadge'
import { NotFound } from '@/NotFound'
import { useCounterparty, useRevealCounterparty } from './queries'
import { StatusBadge } from './StatusBadge'

const dash = (value: string | null): string => value ?? '—'

/** The counterparty as staff see it without a reveal: initials, kind and last-four tails. */
function MaskedCard({ counterparty }: { counterparty: CounterpartyDetail }) {
  const { t } = useTranslation()
  const identifier =
    counterparty.identifier_masked === null ? '—' : `${dash(counterparty.identifier_type)} ${counterparty.identifier_masked}`
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('counterparties.detail.card')}</CardTitle>
      </CardHeader>
      <CardContent>
        <KeyValues
          items={[
            [t('counterparties.table.name'), counterparty.initials === '' ? t('counterparties.unnamed') : counterparty.initials],
            [t('counterparties.table.kind'), t(`counterparties.kind.${counterparty.kind}`)],
            [t('counterparties.table.status'), <StatusBadge key="status" status={counterparty.screening_status} />],
            [t('counterparties.detail.account'), <span key="account" className="font-mono text-xs">{dash(counterparty.account_masked)}</span>],
            [t('counterparties.table.institution'), dash(counterparty.institution)],
            [t('counterparties.table.country'), dash(counterparty.country)],
            [
              t('counterparties.detail.wallet'),
              <span key="wallet" className="font-mono text-xs">
                {counterparty.wallet_masked === null ? '—' : `${counterparty.wallet_network ?? ''} ${counterparty.wallet_masked}`.trim()}
              </span>,
            ],
            [t('counterparties.detail.identifier'), <span key="id" className="font-mono text-xs">{identifier}</span>],
            [
              t('counterparties.detail.screenedAt'),
              counterparty.screened_at === null ? '—' : <DateTimeCell key="screened" value={counterparty.screened_at} />,
            ],
            [t('counterparties.table.firstSeen'), <DateTimeCell key="first" value={counterparty.first_seen_at} />],
            [t('counterparties.table.lastSeen'), <DateTimeCell key="last" value={counterparty.last_seen_at} />],
            [t('counterparties.table.transactions'), String(counterparty.txn_count)],
          ]}
        />
      </CardContent>
    </Card>
  )
}

function Revealed({ values }: { values: RevealedCounterparty }) {
  const { t } = useTranslation()
  const rows: [string, ReactNode][] = []
  if (values.name !== null) rows.push([t('counterparties.reveal.name'), values.name])
  if (values.account !== null) {
    rows.push([t('counterparties.reveal.account'), <span key="account" className="font-mono text-xs">{values.account}</span>])
  }
  if (values.wallet_address !== null) {
    rows.push([
      t('counterparties.reveal.wallet'),
      <span key="wallet" className="font-mono text-xs break-all">
        {values.wallet_network === null ? values.wallet_address : `${values.wallet_network} ${values.wallet_address}`}
      </span>,
    ])
  }
  if (values.identifier !== null) {
    rows.push([
      t('counterparties.reveal.identifier'),
      <span key="identifier" className="font-mono text-xs">
        {values.identifier_type === null ? values.identifier : `${values.identifier_type} ${values.identifier}`}
      </span>,
    ])
  }
  return (
    <div className="space-y-3">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldCheck className="size-4" aria-hidden />
        {t('counterparties.reveal.notice')}
      </p>
      {rows.length === 0 ? <p className="text-sm text-muted-foreground">{t('counterparties.reveal.empty')}</p> : <KeyValues items={rows} />}
    </div>
  )
}

/**
 * The counterparty's values behind an explicit "Revelar": each reveal is a request the API audits
 * as `VIEW_PII`, one row per value. The plaintext lives only in the mutation's result and
 * "Ocultar" drops it.
 */
function RevealCard({ counterpartyId }: { counterpartyId: string }) {
  const { t } = useTranslation()
  const reveal = useRevealCounterparty(counterpartyId)
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('counterparties.reveal.title')}</CardTitle>
        <CardAction>
          {reveal.isSuccess ? (
            <Button type="button" variant="outline" size="sm" onClick={() => reveal.reset()}>
              <EyeOff aria-hidden />
              {t('counterparties.reveal.hide')}
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate()}>
              <Eye aria-hidden />
              {reveal.isPending ? t('counterparties.reveal.running') : t('counterparties.reveal.button')}
            </Button>
          )}
        </CardAction>
      </CardHeader>
      <CardContent>
        <div aria-live="polite">{reveal.isSuccess && <Revealed values={reveal.data} />}</div>
        {reveal.isError && <ErrorBlock error={reveal.error} onRetry={() => reveal.mutate()} />}
      </CardContent>
    </Card>
  )
}

/** The counterparty's findings, each linking to its detail in the screening queue. */
function FindingsCard({ counterparty }: { counterparty: CounterpartyDetail }) {
  const { t } = useTranslation()
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('counterparties.detail.findings')}</CardTitle>
      </CardHeader>
      <CardContent>
        {counterparty.findings.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('counterparties.detail.findingsEmpty')}</p>
        ) : (
          <ul className="space-y-2">
            {counterparty.findings.map((finding) => (
              <li key={finding.id} className="flex flex-wrap items-center gap-2">
                <Link to={`/screening/matches/${finding.id}`} className="font-mono text-xs underline-offset-4 hover:underline">
                  {finding.list_code}
                </Link>
                <BandBadge band={finding.band} />
                <MatchStatusBadge status={finding.status} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function RunsCard({ runs }: { runs: readonly RunResponse[] }) {
  const { t } = useTranslation()
  const columns: Column<RunResponse>[] = [
    { id: 'createdAt', header: t('counterparties.runs.createdAt'), cell: (row) => <DateTimeCell value={row.created_at} /> },
    { id: 'run', header: t('counterparties.runs.run'), cell: (row) => <span className="font-mono text-xs">{row.id.slice(0, 8)}</span> },
    { id: 'outcome', header: t('counterparties.runs.outcome'), cell: (row) => <SubjectStatusBadge status={row.outcome} /> },
  ]
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('counterparties.runs.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable label={t('counterparties.runs.title')} columns={columns} rows={runs} getRowId={(row) => row.id} empty={t('counterparties.runs.empty')} />
      </CardContent>
    </Card>
  )
}

/** One counterparty (Detalle de contraparte): the masked card, the reveal, its findings and runs. */
function CounterpartyView({ counterpartyId }: { counterpartyId: string }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const counterparty = useCounterparty(counterpartyId)

  let body
  if (counterparty.isError) body = <ErrorBlock error={counterparty.error} onRetry={() => void counterparty.refetch()} />
  else if (counterparty.data === undefined) body = <LoadingBlock rows={6} />
  else {
    const data = counterparty.data
    body = (
      <div className="space-y-6">
        <div className="grid gap-6 lg:grid-cols-2">
          <MaskedCard counterparty={data} />
          <FindingsCard counterparty={data} />
        </div>
        {hasRole(ACCESS.screeningCheck) && <RevealCard counterpartyId={counterpartyId} />}
        <RunsCard runs={data.runs} />
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={t('counterparties.detail.title')}
        actions={
          <Button asChild variant="outline">
            <Link to="/counterparties">
              <ArrowLeft aria-hidden />
              {t('counterparties.detail.back')}
            </Link>
          </Button>
        }
      />
      {body}
    </>
  )
}

export function CounterpartyPage() {
  const { counterpartyId } = useParams()
  if (counterpartyId === undefined || counterpartyId === '') return <NotFound />
  return <CounterpartyView key={counterpartyId} counterpartyId={counterpartyId} />
}
