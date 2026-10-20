import { ScanSearch } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { PartyScreeningStaffResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { BandBadge } from './BandBadge'
import { HitList } from './HitList'
import { DelistedSuggestionBadge, MatchStatusBadge } from './MatchStatusBadge'
import { isStaffCheck, isStaffPartyScreening, useCheck, usePartyScreening } from './queries'
import { SubjectStatusBadge } from './SubjectStatusBadge'

/** The party's open findings, each linking to its detail. */
function Findings({ findings }: { findings: PartyScreeningStaffResponse['findings'] }) {
  const { t } = useTranslation()
  if (findings.length === 0) return <p className="text-sm text-muted-foreground">{t('screening.party.findingsEmpty')}</p>
  return (
    <ul className="space-y-2">
      {findings.map((finding) => (
        <li key={finding.id} className="flex flex-wrap items-center gap-2">
          <Link to={`/screening/matches/${finding.id}`} className="font-mono text-xs underline-offset-4 hover:underline">
            {finding.list_code}
          </Link>
          <BandBadge band={finding.band} />
          <MatchStatusBadge status={finding.status} />
          {finding.suggested_disposition === 'DELISTED' && <DelistedSuggestionBadge />}
        </li>
      ))}
    </ul>
  )
}

/**
 * The party's screening (Cliente › Screening): its status or "never screened", the last run,
 * the lists covered, the open findings with links to them, and (writers) "Revisar ahora", which
 * screens the party at once and shows the hits of that run. The run's answer is not kept once the
 * page lets go of it.
 */
export function PartyScreeningCard({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const screening = usePartyScreening(partyId)
  const check = useCheck()
  const result = check.data?.data

  let body
  if (screening.isError) body = <ErrorBlock error={screening.error} onRetry={() => void screening.refetch()} />
  else if (screening.data === undefined) body = <LoadingBlock rows={3} />
  else {
    const view = screening.data
    const staff = isStaffPartyScreening(view) ? view : undefined
    body = (
      <div className="space-y-4">
        {view.status === null ? (
          <p className="text-sm text-muted-foreground">{t('screening.party.never')}</p>
        ) : (
          <KeyValues
            items={[
              [t('screening.party.status'), <SubjectStatusBadge key="status" status={view.status} />],
              [
                t('screening.party.lastScreened'),
                view.last_screened_at === null ? '—' : <DateTimeCell key="last" value={view.last_screened_at} />,
              ],
              [
                t('screening.party.listsCovered'),
                view.lists_covered.length === 0 ? (
                  t('screening.party.listsNone')
                ) : (
                  <span key="lists" className="font-mono text-xs">
                    {view.lists_covered.join(', ')}
                  </span>
                ),
              ],
              ...(staff ? [[t('screening.party.openFindings'), String(staff.open_finding_count)] as [string, string]] : []),
            ]}
          />
        )}
        {staff && staff.findings.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-sm font-medium">{t('screening.party.findings')}</h3>
            <Findings findings={staff.findings} />
          </section>
        )}
      </div>
    )
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{t('screening.party.title')}</CardTitle>
        {hasRole(ACCESS.screeningCheck) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={check.isPending}
            onClick={() => check.mutate({ party_id: partyId })}
          >
            <ScanSearch aria-hidden />
            {check.isPending ? t('screening.party.running') : t('screening.party.checkNow')}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {body}
        {check.isError && <FormAlert error={check.error} />}
        {result && (
          <section aria-label={t('screening.party.result')} aria-live="polite" className="space-y-2">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              {t('screening.party.result')}
              <SubjectStatusBadge status={result.status} />
            </h3>
            {isStaffCheck(result) && <HitList hits={result.hits} empty={t('screening.hits.empty')} />}
          </section>
        )}
      </CardContent>
    </Card>
  )
}
