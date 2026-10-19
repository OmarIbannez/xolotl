import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  FlaskConical,
  Radio,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { CircuitState, ConnectorMode } from '@/api/types'
import { Badge } from '@/components/ui/badge'

interface Spec {
  variant: 'good' | 'warning' | 'critical' | 'info'
  icon: LucideIcon
}

const MODE: Record<ConnectorMode, Spec> = {
  SANDBOX: { variant: 'info', icon: FlaskConical },
  LIVE: { variant: 'good', icon: Radio },
}

const CIRCUIT: Record<CircuitState, Spec> = {
  CLOSED: { variant: 'good', icon: CircleCheck },
  HALF_OPEN: { variant: 'warning', icon: CircleDashed },
  OPEN: { variant: 'critical', icon: CircleAlert },
}

/** The mode a connector runs in for this tenant, with an icon and a label (never color alone). */
export function ModeBadge({ mode }: { mode: ConnectorMode }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = MODE[mode]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`connectors.mode.${mode}`)}
    </Badge>
  )
}

/** The state of the connector's circuit breaker in the process that answered. */
export function CircuitBadge({ state }: { state: CircuitState }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = CIRCUIT[state]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`connectors.circuit.${state}`)}
    </Badge>
  )
}
