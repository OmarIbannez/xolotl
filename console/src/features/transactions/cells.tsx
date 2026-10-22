import { useTranslation } from 'react-i18next'
import type { TransactionStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { useFormat } from '@/i18n/use-locale'

const STATUS_VARIANT: Record<TransactionStatus, 'muted' | 'good' | 'warning' | 'info'> = {
  RECEIVED: 'muted',
  PROCESSED: 'good',
  PARKED: 'warning',
  BACKFILL: 'info',
}

/** A transaction's status as a labelled badge, with "Tardía" beside it when it was late. */
export function StatusCell({ status, late = false }: { status: TransactionStatus; late?: boolean }) {
  const { t } = useTranslation()
  return (
    <span className="flex flex-wrap gap-1">
      <Badge variant={STATUS_VARIANT[status]}>{t(`transactions.status.${status}`)}</Badge>
      {late && <Badge variant="outline">{t('transactions.late')}</Badge>}
    </span>
  )
}

/** An amount in minor units (both accepted currencies have two decimals) in the active locale. */
export function AmountCell({ minor, currency }: { minor: number; currency: string }) {
  const { locale } = useFormat()
  const text = new Intl.NumberFormat(locale, { style: 'currency', currency, currencyDisplay: 'code' }).format(minor / 100)
  return <span className="tabular-nums">{text}</span>
}
