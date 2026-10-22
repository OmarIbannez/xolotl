import { Ban, Eye, OctagonAlert, SignalHigh, SignalLow, SignalMedium, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { HitOutcome, Severity } from '@/api/types'
import { Badge } from '@/components/ui/badge'

type Variant = 'critical' | 'serious' | 'warning' | 'muted' | 'info'

const SEVERITY: Record<Severity, { variant: Variant; icon: LucideIcon }> = {
  CRITICAL: { variant: 'critical', icon: OctagonAlert },
  HIGH: { variant: 'serious', icon: SignalHigh },
  MEDIUM: { variant: 'warning', icon: SignalMedium },
  LOW: { variant: 'muted', icon: SignalLow },
}

const OUTCOME: Record<HitOutcome, { variant: Variant; icon: LucideIcon }> = {
  BLOCK: { variant: 'critical', icon: Ban },
  ALERT: { variant: 'warning', icon: TriangleAlert },
  REVIEW: { variant: 'info', icon: Eye },
}

/** A hit's severity, with an icon and a label (never color alone). */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = SEVERITY[severity]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`hits.severity.${severity}`)}
    </Badge>
  )
}

/** What a hit asked for: an alert, a block or a review. */
export function OutcomeBadge({ outcome }: { outcome: HitOutcome }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = OUTCOME[outcome]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`hits.outcome.${outcome}`)}
    </Badge>
  )
}

/** A hit's typology codes, as small code chips. */
export function TypologyTags({ typology }: { typology: readonly string[] }) {
  if (typology.length === 0) return <span className="text-muted-foreground">—</span>
  return (
    <ul className="flex flex-wrap gap-1">
      {typology.map((code) => (
        <li key={code}>
          <Badge variant="mono">{code}</Badge>
        </li>
      ))}
    </ul>
  )
}
