import { CircleAlert, CircleCheck, CircleHelp, CircleMinus, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { HitBand } from '@/api/types'
import { Badge } from '@/components/ui/badge'

const BAND: Record<HitBand, { variant: 'critical' | 'warning' | 'muted' | 'good'; icon: LucideIcon }> = {
  ALERT: { variant: 'critical', icon: CircleAlert },
  LOW_CONFIDENCE: { variant: 'warning', icon: CircleHelp },
  AUTO_DISCARDED: { variant: 'muted', icon: CircleMinus },
  WHITELISTED: { variant: 'good', icon: CircleCheck },
}

/** A hit's band, with an icon and a label (never color alone). */
export function BandBadge({ band }: { band: HitBand }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = BAND[band]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`screening.band.${band}`)}
    </Badge>
  )
}
