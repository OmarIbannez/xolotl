import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { PartyPiiResponse } from '@/api/types'
import { CopyButton } from '@/components/common/CopyButton'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { calendarDay } from './dates'
import { useRevealParty } from './queries'

function joined(parts: readonly (string | null | undefined)[]): string {
  return parts.filter((part): part is string => typeof part === 'string' && part !== '').join(' ')
}

function Revealed({ pii }: { pii: PartyPiiResponse }) {
  const { t } = useTranslation()
  const format = useFormat()
  const rows: [string, ReactNode][] = []
  if (pii.given_names !== null) rows.push([t('parties.reveal.givenNames'), pii.given_names])
  const surnames = joined([pii.first_surname, pii.second_surname])
  if (surnames !== '') rows.push([t('parties.reveal.surnames'), surnames])
  if (pii.birth_date !== null) {
    rows.push([
      t('parties.reveal.birthDate'),
      <time key="birth" dateTime={pii.birth_date}>
        {format.date(calendarDay(pii.birth_date))}
      </time>,
    ])
  }
  if (pii.legal_name !== null) rows.push([t('parties.reveal.legalName'), pii.legal_name])
  if (pii.identifiers.length > 0) {
    rows.push([
      t('parties.reveal.identifiers'),
      <ul key="identifiers" className="space-y-1">
        {pii.identifiers.map((identifier) => {
          const type = t(`parties.identifierType.${identifier.id_type}`)
          return (
            <li key={identifier.id} className="flex items-center gap-2 font-mono text-xs">
              <span className="text-muted-foreground">{type}</span>
              <span>{identifier.value}</span>
              <CopyButton
                value={identifier.value}
                label={`${t('common.oneTime.copy')} ${type}`}
                copiedMessage={t('common.oneTime.copiedToast')}
                failedMessage={t('common.oneTime.copyFailed')}
              />
            </li>
          )
        })}
      </ul>,
    ])
  }
  if (pii.contacts.length > 0) {
    rows.push([
      t('parties.reveal.contacts'),
      <ul key="contacts" className="space-y-1">
        {pii.contacts.map((contact) => (
          <li key={contact.id}>
            <span className="font-mono text-xs text-muted-foreground">{contact.kind}</span> {contact.value}
          </li>
        ))}
      </ul>,
    ])
  }
  if (pii.addresses.length > 0) {
    rows.push([
      t('parties.reveal.addresses'),
      <ul key="addresses" className="space-y-1">
        {pii.addresses.map((address) => (
          <li key={address.id}>
            <span className="font-mono text-xs text-muted-foreground">{address.address_type}</span>{' '}
            {joined([
              address.lines.street,
              address.lines.ext_number,
              address.lines.int_number,
              address.lines.neighbourhood,
              address.lines.city,
            ])}
          </li>
        ))}
      </ul>,
    ])
  }
  return (
    <div className="space-y-3">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldCheck className="size-4" aria-hidden />
        {t('parties.reveal.notice')}
      </p>
      <KeyValues items={rows} />
    </div>
  )
}

/**
 * The party's personal data, behind an explicit button: every reveal is a request the API audits
 * as `VIEW_PII`. The plaintext lives only in this mutation's result (never a query, never storage)
 * and Hide drops it.
 */
export function RevealCard({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const reveal = useRevealParty(partyId)

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('parties.reveal.title')}</CardTitle>
        <CardAction>
          {reveal.isSuccess ? (
            <Button type="button" variant="outline" size="sm" onClick={() => reveal.reset()}>
              <EyeOff aria-hidden />
              {t('parties.reveal.hide')}
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate()}>
              <Eye aria-hidden />
              {reveal.isPending ? t('parties.reveal.running') : t('parties.reveal.button')}
            </Button>
          )}
        </CardAction>
      </CardHeader>
      <CardContent>
        <div aria-live="polite">{reveal.isSuccess && <Revealed pii={reveal.data} />}</div>
        {reveal.isError && <ErrorBlock error={reveal.error} onRetry={() => reveal.mutate()} />}
      </CardContent>
    </Card>
  )
}
