import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { isApiError } from '@/api/errors'
import type { ParkedInfo, PeriodKeys, TransactionStaffView } from '@/api/types'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { NotFound } from '@/NotFound'
import { formatMinor } from './amount'
import { CounterpartyCard } from './CounterpartyCard'
import { useRevealTransaction, useTransaction } from './queries'
import { RawPayloadCard } from './RawPayloadCard'
import { LateBadge, TransactionStatusBadge } from './TransactionStatusBadge'
import { UnitAmountsCard } from './UnitAmountsCard'

const mono = (value: string) => <span className="font-mono text-xs">{value}</span>
const orDash = (value: ReactNode | null) => value ?? '—'

/** The canonical fields of the transaction, as the client sent them and as processing resolved them. */
function FieldsCard({ txn }: { txn: TransactionStaffView }) {
  const { t } = useTranslation()
  const format = useFormat()
  const yesNo = (value: boolean) => (value ? t('common.yes') : t('common.no'))
  const asset = txn.asset_qty === null ? null : `${txn.asset_qty} ${txn.asset_code ?? ''}`.trim()
  const geo = [txn.geo_country, txn.geo_admin_area].filter((part): part is string => part !== null).join(' · ')
  const rows: [string, ReactNode][] = [
    [t('transactions.detail.fields.externalId'), mono(txn.external_id)],
    [
      t('transactions.detail.fields.status'),
      <div key="status" className="flex flex-wrap gap-1">
        <TransactionStatusBadge status={txn.status} />
        {txn.late && <LateBadge />}
      </div>,
    ],
    [t('transactions.detail.fields.occurredAt'), format.dateTime(txn.occurred_at)],
    [t('transactions.detail.fields.receivedAt'), format.dateTime(txn.received_at)],
    [t('transactions.detail.fields.processedAt'), txn.processed_at === null ? '—' : format.dateTime(txn.processed_at)],
    [
      t('transactions.detail.fields.party'),
      <Link key="party" to={`/parties/${txn.party_id}`} className="font-mono text-xs underline-offset-4 hover:underline">
        {txn.party_id}
      </Link>,
    ],
    [t('transactions.detail.fields.account'), mono(txn.account_ref)],
    [t('transactions.detail.fields.product'), orDash(txn.product)],
    [t('transactions.detail.fields.direction'), t(`transactions.direction.${txn.direction}`)],
    [t('transactions.detail.fields.channel'), t(`transactions.channel.${txn.channel}`)],
    [t('transactions.detail.fields.instrument'), t(`transactions.instrument.${txn.instrument}`)],
    [t('transactions.detail.fields.rail'), t(`transactions.rail.${txn.rail}`)],
    [t('transactions.detail.fields.amount'), <span key="amount" className="tabular">{formatMinor(txn.amount_minor, txn.currency, format.locale)}</span>],
    [t('transactions.detail.fields.fee'), txn.fee_minor === null ? '—' : formatMinor(txn.fee_minor, txn.currency, format.locale)],
    [t('transactions.detail.fields.asset'), asset === null ? '—' : mono(asset)],
    [t('transactions.detail.fields.purposeCode'), txn.purpose_code === null ? '—' : mono(txn.purpose_code)],
    [t('transactions.detail.fields.tier'), orDash(txn.tier)],
    [t('transactions.detail.fields.riskRating'), orDash(txn.risk_rating)],
    [t('transactions.detail.fields.monitoringProfile'), txn.monitoring_profile],
    [t('transactions.detail.fields.snapshot'), txn.policy_snapshot_id === null ? '—' : mono(txn.policy_snapshot_id)],
    [t('transactions.detail.fields.batch'), mono(txn.batch_id)],
    [t('transactions.detail.fields.device'), yesNo(txn.device_present)],
    [t('transactions.detail.fields.geo'), geo === '' ? '—' : geo],
  ]
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('transactions.detail.fields.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        <KeyValues items={rows} />
      </CardContent>
    </Card>
  )
}

/** The period keys processing gave the transaction (Mexico City day, month, quarter). */
function PeriodsCard({ periods }: { periods: PeriodKeys | null }) {
  const { t } = useTranslation()
  const title = t('transactions.detail.periods.title')
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {periods === null ? (
          <p className="text-sm text-muted-foreground">{t('transactions.detail.periods.empty')}</p>
        ) : (
          <KeyValues
            items={[
              [t('transactions.detail.periods.DAY'), mono(periods.DAY)],
              [t('transactions.detail.periods.MONTH'), mono(periods.MONTH)],
              [t('transactions.detail.periods.QUARTER'), mono(periods.QUARTER)],
              [t('transactions.detail.periods.BUSINESS_DAY'), periods.BUSINESS_DAY ? t('common.yes') : t('common.no')],
            ]}
          />
        )}
      </CardContent>
    </Card>
  )
}

/** Why a parked transaction waits, and since when. */
function ParkedCard({ parked }: { parked: ParkedInfo }) {
  const { t } = useTranslation()
  const title = t('transactions.detail.parked.title')
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <KeyValues
          items={[
            [t('transactions.detail.parked.reason'), t(`transactions.parkReason.${parked.reason}`)],
            [t('transactions.detail.parked.unit'), parked.unit_code === null ? '—' : mono(parked.unit_code)],
            [t('transactions.detail.parked.neededOn'), parked.needed_on === null ? '—' : mono(parked.needed_on)],
            [t('transactions.detail.parked.attempts'), <span key="attempts" className="tabular">{parked.attempts}</span>],
            [t('transactions.detail.parked.parkedAt'), <DateTimeCell key="at" value={parked.parked_at} />],
            [t('transactions.detail.parked.lastError'), parked.last_error === null ? '—' : mono(parked.last_error)],
          ]}
        />
      </CardContent>
    </Card>
  )
}

/**
 * Detalle de transacción: the canonical fields, the unit amounts with the unit values used,
 * the period keys, the masked counterparty, why it is parked if it is, and the payload as
 * sent. The counterparty's values and the payload come from one audited reveal.
 */
function TransactionDetail({ transactionId }: { transactionId: string }) {
  const { t } = useTranslation()
  const txn = useTransaction(transactionId)
  const reveal = useRevealTransaction(transactionId)

  if (isApiError(txn.error) && txn.error.status === 404) return <NotFound />

  let body: ReactNode
  if (txn.isError) body = <ErrorBlock error={txn.error} onRetry={() => void txn.refetch()} />
  else if (txn.data === undefined) body = <LoadingBlock rows={6} />
  else {
    const data = txn.data
    body = (
      <div className="space-y-6">
        {data.parked !== null && <ParkedCard parked={data.parked} />}
        <div className="grid gap-6 lg:grid-cols-2">
          <FieldsCard txn={data} />
          <div className="space-y-6">
            <CounterpartyCard
              counterparty={data.counterparty}
              statusAtProcessing={data.counterparty_screening_status_at_processing}
              reveal={reveal}
            />
            <PeriodsCard periods={data.period_keys} />
          </div>
        </div>
        <UnitAmountsCard amounts={data.unit_amounts} />
        <RawPayloadCard reveal={reveal} />
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={t('transactions.detail.title')}
        actions={
          <Button asChild variant="outline">
            <Link to="/transactions">
              <ArrowLeft aria-hidden />
              {t('transactions.detail.back')}
            </Link>
          </Button>
        }
      />
      {body}
    </>
  )
}

export function TransactionPage() {
  const { transactionId } = useParams()
  if (transactionId === undefined || transactionId === '') return <NotFound />
  return <TransactionDetail key={transactionId} transactionId={transactionId} />
}
