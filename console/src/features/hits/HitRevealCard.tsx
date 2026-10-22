import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { HitReveal, HitTransactionView, RevealedCounterparty } from '@/api/types'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useRevealHit } from './queries'

function joined(parts: readonly (string | null)[]): string {
  return parts.filter((part): part is string => part !== null && part !== '').join(' ')
}

function CounterpartyValues({ label, values }: { label: string; values: RevealedCounterparty }) {
  const { t } = useTranslation()
  const rows: [string, ReactNode][] = []
  if (values.name !== null) rows.push([t('hits.reveal.name'), values.name])
  if (values.account !== null) {
    rows.push([t('hits.reveal.account'), <span key="account" className="font-mono text-xs">{values.account}</span>])
  }
  if (values.wallet_address !== null) {
    rows.push([
      t('hits.reveal.wallet'),
      <span key="wallet" className="font-mono text-xs break-all">
        {values.wallet_network === null ? values.wallet_address : `${values.wallet_network} · ${values.wallet_address}`}
      </span>,
    ])
  }
  if (values.identifier !== null) {
    rows.push([
      t('hits.reveal.identifier'),
      <span key="identifier" className="font-mono text-xs">
        {values.identifier_type === null ? values.identifier : `${values.identifier_type} ${values.identifier}`}
      </span>,
    ])
  }
  return (
    <li className="space-y-1">
      <p className="text-xs text-muted-foreground">{label}</p>
      <KeyValues items={rows} />
    </li>
  )
}

function Revealed({ reveal, transactions }: { reveal: HitReveal; transactions: readonly HitTransactionView[] }) {
  const { t } = useTranslation()
  const { party } = reveal
  const name = party.legal_name ?? joined([party.given_names, party.first_surname, party.second_surname])
  const rows: [string, ReactNode][] = []
  if (name !== '') rows.push([t('hits.reveal.name'), name])
  if (party.identifiers.length > 0) {
    rows.push([
      t('hits.reveal.identifiers'),
      <ul key="identifiers" className="space-y-0.5 font-mono text-xs">
        {party.identifiers.map((identifier) => (
          <li key={identifier.id}>
            <span className="text-muted-foreground">{t(`parties.identifierType.${identifier.id_type}`)}</span> {identifier.value}
          </li>
        ))}
      </ul>,
    ])
  }
  // Each revealed counterparty is labelled as the masked table shows it: kind and institution.
  const counterparties = Object.entries(reveal.counterparties).map(([id, values]) => {
    const masked = transactions.find((txn) => txn.counterparty?.id === id)?.counterparty
    const label =
      masked === undefined || masked === null
        ? id.slice(0, 8)
        : joined([t(`transactions.counterpartyKind.${masked.kind}`), masked.institution])
    return { id, label, values }
  })

  return (
    <div className="space-y-4">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldCheck className="size-4" aria-hidden />
        {t('hits.reveal.notice')}
      </p>
      {rows.length === 0 && counterparties.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('hits.reveal.empty')}</p>
      ) : (
        <>
          {rows.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">{t('hits.reveal.party')}</h3>
              <KeyValues items={rows} />
            </section>
          )}
          {counterparties.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">{t('hits.reveal.counterparties')}</h3>
              <ul className="space-y-3">
                {counterparties.map(({ id, label, values }) => (
                  <CounterpartyValues key={id} label={label} values={values} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  )
}

/**
 * The hit's personal values (the party's name and identifiers, the counterparties of the shown
 * transactions), behind an explicit "Revelar": each reveal is a request the API audits as
 * `VIEW_PII`. The plaintext lives only in the mutation's result and "Ocultar" drops it.
 */
export function HitRevealCard({ hitId, transactions }: { hitId: string; transactions: readonly HitTransactionView[] }) {
  const { t } = useTranslation()
  const reveal = useRevealHit(hitId)

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('hits.reveal.title')}</CardTitle>
        <CardAction>
          {reveal.isSuccess ? (
            <Button type="button" variant="outline" size="sm" onClick={() => reveal.reset()}>
              <EyeOff aria-hidden />
              {t('hits.reveal.hide')}
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate()}>
              <Eye aria-hidden />
              {reveal.isPending ? t('hits.reveal.running') : t('hits.reveal.button')}
            </Button>
          )}
        </CardAction>
      </CardHeader>
      <CardContent>
        <div aria-live="polite">{reveal.isSuccess && <Revealed reveal={reveal.data} transactions={transactions} />}</div>
        {reveal.isError && <ErrorBlock error={reveal.error} onRetry={() => reveal.mutate()} />}
      </CardContent>
    </Card>
  )
}
