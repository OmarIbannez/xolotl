import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import type { PartyResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { NotFound } from '@/NotFound'
import { calendarDay } from './dates'
import { LegalHoldCard } from './LegalHoldCard'
import { PartyStatusBadge } from './PartyStatusBadge'
import { useParty } from './queries'
import { RevealCard } from './RevealCard'

function Summary({ party }: { party: PartyResponse }) {
  const { t } = useTranslation()
  const format = useFormat()
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{party.display_name ?? t('parties.table.noName')}</CardTitle>
      </CardHeader>
      <CardContent>
        <KeyValues
          items={[
            [t('parties.detail.type'), t(`parties.type.${party.party_type}`)],
            [t('parties.detail.status'), <PartyStatusBadge key="status" status={party.status} />],
            [t('parties.detail.country'), format.region(party.country_code)],
            [
              t('parties.detail.identifiers'),
              <ul key="identifiers" className="space-y-0.5">
                {party.identifiers.map((identifier) => (
                  <li key={identifier.id} className="font-mono text-xs">
                    <span className="text-muted-foreground">{t(`parties.identifierType.${identifier.id_type}`)}</span>{' '}
                    {identifier.masked_value}
                  </li>
                ))}
              </ul>,
            ],
            [
              t('parties.detail.retentionUntil'),
              party.retention_until === null ? (
                '—'
              ) : (
                <time key="retention" dateTime={party.retention_until}>
                  {format.date(calendarDay(party.retention_until))}
                </time>
              ),
            ],
            [
              t('parties.detail.legalHold'),
              party.legal_hold ? (
                <Badge key="hold" variant="warning">
                  {t('parties.detail.legalHoldOn')}
                </Badge>
              ) : (
                t('parties.detail.legalHoldOff')
              ),
            ],
          ]}
        />
      </CardContent>
    </Card>
  )
}

/** One party: its masked summary, the explicit reveal of its personal data and (MLRO) its legal hold. */
function PartyDetail({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const party = useParty(partyId)

  let body
  if (party.isError) body = <ErrorBlock error={party.error} onRetry={() => void party.refetch()} />
  else if (party.data === undefined) body = <LoadingBlock rows={6} />
  else {
    body = (
      <div className="space-y-6">
        <Summary party={party.data} />
        {hasRole(ACCESS.parties) && <RevealCard partyId={partyId} />}
        {hasRole(ACCESS.legalHold) && <LegalHoldCard partyId={partyId} legalHold={party.data.legal_hold} />}
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={t('parties.detail.title')}
        actions={
          <Button asChild variant="outline">
            <Link to="/parties">
              <ArrowLeft aria-hidden />
              {t('parties.title')}
            </Link>
          </Button>
        }
      />
      {body}
    </>
  )
}

export function PartyPage() {
  const { partyId } = useParams()
  if (partyId === undefined || partyId === '') return <NotFound />
  return <PartyDetail key={partyId} partyId={partyId} />
}
