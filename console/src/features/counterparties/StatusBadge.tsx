import { CircleAlert, CircleCheck, CircleDashed, CircleDot, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { CounterpartyScreeningStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'

const STATUS: Record<
  CounterpartyScreeningStatus,
  { variant: 'muted' | 'good' | 'warning' | 'critical'; icon: LucideIcon }
> = {
  UNSCREENED: { variant: 'muted', icon: CircleDashed },
  CLEAR: { variant: 'good', icon: CircleCheck },
  PENDING_REVIEW: { variant: 'warning', icon: CircleDot },
  TRUE_MATCH: { variant: 'critical', icon: CircleAlert },
}

/** A counterparty's screening status, with an icon and a label. */
export function StatusBadge({ status }: { status: CounterpartyScreeningStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`counterparties.status.${status}`)}
    </Badge>
  )
}
