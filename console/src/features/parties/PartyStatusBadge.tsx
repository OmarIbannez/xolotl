import { CircleCheck, GitMerge, Trash2, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { PartyStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'

const STATUS: Record<PartyStatus, { variant: 'good' | 'info' | 'muted'; icon: LucideIcon }> = {
  ACTIVE: { variant: 'good', icon: CircleCheck },
  MERGED: { variant: 'info', icon: GitMerge },
  PURGED: { variant: 'muted', icon: Trash2 },
}

/** A party's status, with an icon and a label (never color alone). */
export function PartyStatusBadge({ status }: { status: PartyStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`parties.status.${status}`)}
    </Badge>
  )
}
