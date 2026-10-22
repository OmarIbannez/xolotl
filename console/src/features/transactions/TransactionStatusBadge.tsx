import { CircleCheck, Clock, Hourglass, Inbox, PauseCircle, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { TransactionStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'

const STATUS: Record<TransactionStatus, { variant: 'info' | 'good' | 'warning' | 'muted'; icon: LucideIcon }> = {
  RECEIVED: { variant: 'info', icon: Inbox },
  PROCESSED: { variant: 'good', icon: CircleCheck },
  PARKED: { variant: 'warning', icon: PauseCircle },
  BACKFILL: { variant: 'muted', icon: Hourglass },
}

/** A transaction's processing status, with an icon and a label (never color alone). */
export function TransactionStatusBadge({ status }: { status: TransactionStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`transactions.status.${status}`)}
    </Badge>
  )
}

/** Marks a transaction that arrived after later ones of its party, or more than a day after it happened. */
export function LateBadge() {
  const { t } = useTranslation()
  return (
    <Badge variant="muted">
      <Clock aria-hidden />
      {t('transactions.lateBadge')}
    </Badge>
  )
}
