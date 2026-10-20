import { CircleAlert, CircleCheck, CircleDashed, Clock, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { ListOverview } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { errorCodeOf, freshnessOf, type Freshness } from './freshness'

const SPEC: Record<Freshness, { variant: 'good' | 'warning' | 'critical' | 'muted'; icon: LucideIcon }> = {
  FRESH: { variant: 'good', icon: CircleCheck },
  STALE: { variant: 'warning', icon: Clock },
  FAILING: { variant: 'critical', icon: CircleAlert },
  EMPTY: { variant: 'muted', icon: CircleDashed },
}

/**
 * The freshness of a list with an icon and a label (never color alone). A failing list also
 * shows its error code, and the whole `last_error` is the badge's tooltip.
 */
export function FreshnessBadge({ row }: { row: ListOverview }) {
  const { t } = useTranslation()
  const freshness = freshnessOf(row)
  const { variant, icon: Icon } = SPEC[freshness]
  const code = freshness === 'FAILING' ? errorCodeOf(row.last_error) : null
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" title={row.last_error ?? undefined}>
      <Badge variant={variant}>
        <Icon aria-hidden />
        {t(`lists.freshness.${freshness}`)}
      </Badge>
      {code && <span className="font-mono text-xs text-muted-foreground">{code}</span>}
    </span>
  )
}
