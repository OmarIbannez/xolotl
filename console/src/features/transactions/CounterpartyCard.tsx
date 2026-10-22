import { CircleAlert, CircleCheck, CircleDashed, CircleDot, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { CounterpartyScreeningStatus, CounterpartySummary, RevealedCounterparty } from '@/api/types'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { RevealAction, RevealNotice, type TransactionReveal } from './RawPayloadCard'

const SCREENING: Record<CounterpartyScreeningStatus, { variant: 'muted' | 'good' | 'warning' | 'critical'; icon: LucideIcon }> = {
  UNSCREENED: { variant: 'muted', icon: CircleDashed },
  CLEAR: { variant: 'good', icon: CircleCheck },
  PENDING_REVIEW: { variant: 'warning', icon: CircleDot },
  TRUE_MATCH: { variant: 'critical', icon: CircleAlert },
}

/** A counterparty's list screening status, with an icon and a label (never color alone). */
export function CounterpartyScreeningBadge({ status }: { status: CounterpartyScreeningStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = SCREENING[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`transactions.screeningStatus.${status}`)}
    </Badge>
  )
}

const ON_RECORD = ['has_name', 'has_account', 'has_wallet', 'has_identifier'] as const

function Revealed({ values }: { values: RevealedCounterparty }) {
  const { t } = useTranslation()
  const rows: [string, ReactNode][] = []
  if (values.name !== null) rows.push([t('transactions.detail.counterparty.name'), values.name])
  if (values.account !== null) {
    rows.push([t('transactions.detail.counterparty.account'), <span key="account" className="font-mono text-xs">{values.account}</span>])
  }
  if (values.wallet_address !== null) {
    rows.push([
      t('transactions.detail.counterparty.wallet'),
      <span key="wallet" className="font-mono text-xs break-all">
        {values.wallet_network === null ? values.wallet_address : `${values.wallet_network} · ${values.wallet_address}`}
      </span>,
    ])
  }
  if (values.identifier !== null) {
    rows.push([
      t('transactions.detail.counterparty.identifier'),
      <span key="identifier" className="font-mono text-xs">
        {values.identifier_type === null ? values.identifier : `${values.identifier_type} ${values.identifier}`}
      </span>,
    ])
  }
  return (
    <div className="space-y-3">
      <RevealNotice />
      <KeyValues items={rows} />
    </div>
  )
}

interface CounterpartyCardProps {
  counterparty: CounterpartySummary | null
  /** The counterparty's screening status when the transaction was processed. */
  statusAtProcessing: CounterpartyScreeningStatus | null
  reveal: TransactionReveal
}

/**
 * The counterparty, masked: its kind, institution and country, which values are on record and
 * its screening status. "Revelar" asks for the values themselves, audited as `VIEW_PII`.
 */
export function CounterpartyCard({ counterparty, statusAtProcessing, reveal }: CounterpartyCardProps) {
  const { t } = useTranslation()
  const title = t('transactions.detail.counterparty.title')
  const revealed = reveal.isSuccess ? reveal.data.counterparty_revealed : null

  let body: ReactNode
  if (counterparty === null) {
    body = <p className="text-sm text-muted-foreground">{t('transactions.detail.counterparty.none')}</p>
  } else {
    const onRecord = ON_RECORD.filter((key) => counterparty[key])
    body = (
      <div className="space-y-4">
        <KeyValues
          items={[
            [t('transactions.detail.counterparty.kind'), t(`transactions.counterpartyKind.${counterparty.kind}`)],
            [t('transactions.detail.counterparty.institution'), counterparty.institution ?? '—'],
            [t('transactions.detail.counterparty.country'), counterparty.country ?? '—'],
            ...(counterparty.wallet_network === null
              ? []
              : ([[t('transactions.detail.counterparty.walletNetwork'), counterparty.wallet_network]] as [string, ReactNode][])),
            [
              t('transactions.detail.counterparty.onRecord'),
              onRecord.length === 0 ? (
                t('transactions.detail.counterparty.nothingOnRecord')
              ) : (
                <ul key="on-record" className="flex flex-wrap gap-1">
                  {onRecord.map((key) => (
                    <li key={key}>
                      <Badge variant="muted">{t(`transactions.detail.counterparty.${key}`)}</Badge>
                    </li>
                  ))}
                </ul>
              ),
            ],
            [
              t('transactions.detail.counterparty.screeningStatus'),
              <CounterpartyScreeningBadge key="status" status={counterparty.screening_status} />,
            ],
            [
              t('transactions.detail.counterparty.statusAtProcessing'),
              statusAtProcessing === null ? '—' : <CounterpartyScreeningBadge key="at" status={statusAtProcessing} />,
            ],
            [t('transactions.detail.counterparty.txnCount'), <span key="count" className="tabular">{counterparty.txn_count}</span>],
          ]}
        />
        <div aria-live="polite">{revealed !== null && <Revealed values={revealed} />}</div>
        {reveal.isError && <ErrorBlock error={reveal.error} onRetry={() => reveal.mutate()} />}
      </div>
    )
  }

  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {counterparty !== null && (
          <CardAction>
            <RevealAction reveal={reveal} />
          </CardAction>
        )}
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  )
}
