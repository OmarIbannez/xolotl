import { ArrowUpCircle, Ban, CircleCheck, CircleDot, Repeat, ShieldAlert, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { MatchStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'

const STATUS: Record<MatchStatus, { variant: 'warning' | 'serious' | 'good' | 'critical' | 'muted'; icon: LucideIcon }> = {
  OPEN: { variant: 'warning', icon: CircleDot },
  ESCALATED: { variant: 'serious', icon: ArrowUpCircle },
  FALSE_POSITIVE: { variant: 'good', icon: CircleCheck },
  TRUE_MATCH: { variant: 'critical', icon: ShieldAlert },
  DELISTED: { variant: 'muted', icon: Ban },
  SUPERSEDED: { variant: 'muted', icon: Repeat },
}

/** A finding's status, with an icon and a label (never color alone). */
export function MatchStatusBadge({ status }: { status: MatchStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`screening.status.${status}`)}
    </Badge>
  )
}

/** The system's suggestion that the entry left its list (`suggested_disposition = DELISTED`). */
export function DelistedSuggestionBadge() {
  const { t } = useTranslation()
  return (
    <Badge variant="info">
      <Ban aria-hidden />
      {t('screening.suggested.DELISTED')}
    </Badge>
  )
}
