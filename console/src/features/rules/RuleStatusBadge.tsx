import { Archive, CircleCheck, CircleDot, FlaskConical, Hourglass, PencilLine, ShieldCheck, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { RuleVersionStatus, SimulationResponse } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { useFormat } from '@/i18n/use-locale'
import { simulationValid } from './queries'

type Variant = 'good' | 'warning' | 'info' | 'muted' | 'serious'

const STATUS: Record<RuleVersionStatus, { variant: Variant; icon: LucideIcon }> = {
  DRAFT: { variant: 'muted', icon: PencilLine },
  SIMULATED: { variant: 'info', icon: FlaskConical },
  IN_REVIEW: { variant: 'warning', icon: Hourglass },
  APPROVED: { variant: 'info', icon: ShieldCheck },
  ACTIVE: { variant: 'good', icon: CircleCheck },
  RETIRED: { variant: 'muted', icon: Archive },
}

/** A rule version's lifecycle status, with an icon and a label (never color alone). */
export function RuleStatusBadge({ status }: { status: RuleVersionStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`rules.status.${status}`)}
    </Badge>
  )
}

/** The age badge of a completed simulation: "válida hasta …" or "vencida". */
export function SimulationAgeBadge({ simulation }: { simulation: SimulationResponse }) {
  const { t } = useTranslation()
  const format = useFormat()
  if (simulation.status !== 'COMPLETED' || simulation.valid_until === null) return null
  // A Mexico City day: read at noon UTC, so no time zone moves it to another day.
  const until = format.date(`${simulation.valid_until}T12:00:00Z`)
  return simulationValid(simulation) ? (
    <Badge variant="good">
      <CircleDot aria-hidden />
      {t('rules.simulation.validUntil', { date: until })}
    </Badge>
  ) : (
    <Badge variant="serious">
      <CircleDot aria-hidden />
      {t('rules.simulation.expired', { date: until })}
    </Badge>
  )
}
