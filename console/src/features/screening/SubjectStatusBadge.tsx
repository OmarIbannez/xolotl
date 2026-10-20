import { CircleAlert, CircleCheck, CircleDot, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { SubjectStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'

const STATUS: Record<SubjectStatus, { variant: 'good' | 'warning' | 'critical'; icon: LucideIcon }> = {
  CLEAR: { variant: 'good', icon: CircleCheck },
  PENDING_REVIEW: { variant: 'warning', icon: CircleDot },
  TRUE_MATCH: { variant: 'critical', icon: CircleAlert },
}

/** A subject's screening status (a party's or an ad hoc check's), with an icon and a label. */
export function SubjectStatusBadge({ status }: { status: SubjectStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`screening.subjectStatus.${status}`)}
    </Badge>
  )
}
