import { ArrowLeft } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import type { HitHistoryItem, MatchDetail } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { arrayOf, entityType, identifier, isObject, nameOf, partialDate, strings, textOf, type Json } from '@/features/lists/entry-json'
import { NotFound } from '@/NotFound'
import { BandBadge } from './BandBadge'
import { DispositionForm } from './DispositionForm'
import { ExplanationTable } from './ExplanationTable'
import { MatchRevealCard } from './MatchRevealCard'
import { DelistedSuggestionBadge, MatchStatusBadge } from './MatchStatusBadge'
import { useMatch, useRevokeWhitelist } from './queries'

const KEEP = 4

/** An identifier value with all but its last four characters hidden. */
function masked(value: string): string {
  return value.length <= KEEP ? '•'.repeat(value.length) : `${'•'.repeat(value.length - KEEP)}${value.slice(-KEEP)}`
}

function List({ values }: { values: (string | null)[] }) {
  const present = values.filter((value): value is string => value !== null)
  if (present.length === 0) return <span className="text-muted-foreground">—</span>
  return (
    <ul className="space-y-0.5">
      {present.map((value, index) => (
        <li key={index}>{value}</li>
      ))}
    </ul>
  )
}

/** The subject as the detail shows it: initials and masked identifiers only. */
function SubjectCard({ match }: { match: MatchDetail }) {
  const { t } = useTranslation()
  const { subject } = match
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.match.subject')}</CardTitle>
        {subject.party_id !== null && (
          <CardAction>
            <Button asChild variant="outline" size="sm">
              <Link to={`/parties/${subject.party_id}`}>{t('screening.match.openParty')}</Link>
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>
        <KeyValues
          items={[
            [t('screening.match.kind'), t(`screening.match.kinds.${subject.kind}`)],
            [t('screening.match.entityType'), entityType(t, subject.entity_type) ?? '—'],
            [t('screening.match.initials'), subject.initials === '' ? '—' : subject.initials],
            [
              t('screening.match.identifiers'),
              <ul key="ids" className="space-y-0.5 font-mono text-xs">
                {subject.identifiers.length === 0 ? <li>—</li> : subject.identifiers.map((id) => <li key={id}>{id}</li>)}
              </ul>,
            ],
          ]}
        />
      </CardContent>
    </Card>
  )
}

/** The list entry: public data of a shared list, its identifiers masked. A private entry only shows when revealed. */
function EntryCard({ match }: { match: MatchDetail }) {
  const { t } = useTranslation()
  const ref = match.latest_hit.features.entry_ref
  const isPrivate = isObject(ref) && 'private_list_entry_id' in ref
  const entry: Json | null = match.entry

  let body: ReactNode
  if (entry === null) {
    body = <p className="text-sm text-muted-foreground">{isPrivate ? t('screening.match.privateEntry') : t('screening.match.noEntry')}</p>
  } else {
    const ids = arrayOf(entry.identifiers).map((value) =>
      isObject(value) && typeof value.value === 'string' ? identifier(t, { ...value, value: masked(value.value) }) : null,
    )
    body = (
      <KeyValues
        items={[
          [t('lists.entry.name'), nameOf(entry.primary_name) ?? '—'],
          [t('lists.entry.entityType'), entityType(t, entry.entity_type) ?? '—'],
          [t('lists.entry.aliases'), <List key="aliases" values={arrayOf(entry.aliases).map(nameOf)} />],
          [t('lists.entry.birthDates'), <List key="births" values={arrayOf(entry.birth_dates).map(partialDate)} />],
          [t('lists.entry.nationalities'), <List key="nat" values={strings(entry.nationalities)} />],
          [t('lists.entry.identifiers'), <List key="ids" values={ids} />],
          [t('lists.entry.programs'), <List key="programs" values={strings(entry.programs)} />],
          [t('lists.entry.listedOn'), textOf(entry.listed_on) ?? '—'],
        ]}
      />
    )
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.match.entry')}</CardTitle>
        <CardDescription className="font-mono text-xs">{match.list_code}</CardDescription>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  )
}

/** The finding's status, band and score, with the MLRO's whitelist revocation when one applies. */
function SummaryCard({ match }: { match: MatchDetail }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const revoke = useRevokeWhitelist()
  const [asking, setAsking] = useState(false)
  const whitelistId = match.whitelist_id

  function cancel() {
    revoke.reset()
    setAsking(false)
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.match.summary')}</CardTitle>
        {whitelistId !== null && hasRole(ACCESS.whitelistRevoke) && (
          <CardAction>
            <Button type="button" variant="outline" size="sm" onClick={() => setAsking(true)}>
              {t('screening.whitelist.revoke')}
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>
        <KeyValues
          items={[
            [
              t('screening.match.status'),
              <div key="status" className="flex flex-wrap gap-1">
                <MatchStatusBadge status={match.status} />
                {match.suggested_disposition === 'DELISTED' && <DelistedSuggestionBadge />}
              </div>,
            ],
            [t('screening.match.band'), <BandBadge key="band" band={match.band} />],
            [t('screening.match.score'), <span key="score" className="tabular">{match.score}</span>],
            [t('screening.match.list'), <span key="list" className="font-mono text-xs">{match.list_code}</span>],
            [t('screening.match.createdAt'), <DateTimeCell key="created" value={match.created_at} />],
            [t('screening.match.updatedAt'), <DateTimeCell key="updated" value={match.updated_at} />],
          ]}
        />
      </CardContent>
      {asking && whitelistId !== null && (
        <ConfirmDialog
          title={t('screening.whitelist.revoke')}
          description={t('screening.whitelist.revokeDescription')}
          confirmLabel={t('screening.whitelist.revoke')}
          pendingLabel={t('screening.whitelist.revokePending')}
          pending={revoke.isPending}
          alert={revoke.isError ? <FormAlert error={revoke.error} /> : undefined}
          onConfirm={() =>
            revoke.mutate(whitelistId, {
              onSuccess: () => {
                toast.success(t('screening.whitelist.revoked'))
                setAsking(false)
              },
            })
          }
          onCancel={cancel}
        />
      )}
    </Card>
  )
}

function HistoryCard({ match }: { match: MatchDetail }) {
  const { t } = useTranslation()
  const columns: Column<HitHistoryItem>[] = [
    { id: 'createdAt', header: t('screening.match.createdAt'), cell: (row) => <DateTimeCell value={row.created_at} /> },
    { id: 'run', header: t('screening.match.run'), cell: (row) => <span className="font-mono text-xs">{row.run_id.slice(0, 8)}</span> },
    { id: 'score', header: t('screening.match.score'), cell: (row) => <span className="tabular">{row.score}</span>, className: 'text-right' },
    { id: 'band', header: t('screening.match.band'), cell: (row) => <BandBadge band={row.band} /> },
  ]
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.match.history')}</CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable
          label={t('screening.match.historyLabel')}
          columns={columns}
          rows={match.history}
          getRowId={(row) => row.hit_id}
          empty="—"
        />
      </CardContent>
    </Card>
  )
}

function TimelineCard({ match }: { match: MatchDetail }) {
  const { t } = useTranslation()
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.match.timeline')}</CardTitle>
      </CardHeader>
      <CardContent>
        {match.dispositions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('screening.match.timelineEmpty')}</p>
        ) : (
          <ol className="space-y-3 border-l pl-4">
            {match.dispositions.map((d) => (
              <li key={d.id} className="text-sm">
                <p className="font-medium">{t(`screening.decision.${d.decision}`)}</p>
                <p className="text-xs text-muted-foreground">
                  <DateTimeCell value={d.decided_at} /> · {t('screening.match.decidedBy')}{' '}
                  {d.decided_by_type === 'SYSTEM' ? (
                    t('screening.match.system')
                  ) : (
                    <span className="font-mono">{d.decided_by?.slice(0, 8) ?? t('screening.match.staffMember')}</span>
                  )}
                </p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * One finding (Detalle de coincidencia): the masked subject beside the list entry, the
 * explanation of the latest hit, the hit history, the decisions so far, the decision form for
 * those who decide, and the audited reveal of the personal values.
 */
function MatchDetailView({ matchId }: { matchId: string }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const match = useMatch(matchId)

  let body
  if (match.isError) body = <ErrorBlock error={match.error} onRetry={() => void match.refetch()} />
  else if (match.data === undefined) body = <LoadingBlock rows={6} />
  else {
    const data = match.data
    body = (
      <div className="space-y-6">
        <SummaryCard match={data} />
        <div className="grid gap-6 lg:grid-cols-2">
          <SubjectCard match={data} />
          <EntryCard match={data} />
        </div>
        <Card className="animate-fade-in-up">
          <CardHeader>
            <CardTitle>{t('screening.match.explanation.title')}</CardTitle>
            <CardDescription>{t('screening.match.explanation.description')}</CardDescription>
          </CardHeader>
          <CardContent>
            <ExplanationTable hit={data.latest_hit} />
          </CardContent>
        </Card>
        <div className="grid gap-6 lg:grid-cols-2">
          <HistoryCard match={data} />
          <TimelineCard match={data} />
        </div>
        {hasRole(ACCESS.dispositions) && <DispositionForm match={data} />}
        <MatchRevealCard matchId={matchId} dispositions={data.dispositions} />
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={t('screening.match.title')}
        actions={
          <Button asChild variant="outline">
            <Link to="/screening/matches">
              <ArrowLeft aria-hidden />
              {t('screening.match.back')}
            </Link>
          </Button>
        }
      />
      {body}
    </>
  )
}

export function MatchPage() {
  const { matchId } = useParams()
  if (matchId === undefined || matchId === '') return <NotFound />
  return <MatchDetailView key={matchId} matchId={matchId} />
}
