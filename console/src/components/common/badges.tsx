import {
  Ban,
  CircleAlert,
  CircleCheck,
  FlaskConical,
  Hourglass,
  Lock,
  Rocket,
  UserRound,
  UserRoundX,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { ApiKeyStatus, Role, StaffStatus, TenantEnv, TenantStatus } from '@/api/types'
import { Badge } from '@/components/ui/badge'

type Variant = 'default' | 'good' | 'warning' | 'critical' | 'info' | 'muted'

interface Spec {
  variant: Variant
  icon: LucideIcon
}

const ENV: Record<TenantEnv, Spec> = {
  SANDBOX: { variant: 'warning', icon: FlaskConical },
  PROD: { variant: 'default', icon: Rocket },
}

const TENANT_STATUS: Record<TenantStatus, Spec> = {
  ONBOARDING: { variant: 'info', icon: Hourglass },
  ACTIVE: { variant: 'good', icon: CircleCheck },
  SUSPENDED: { variant: 'critical', icon: CircleAlert },
  OFFBOARDED: { variant: 'muted', icon: Lock },
}

const API_KEY_STATUS: Record<ApiKeyStatus, Spec> = {
  ACTIVE: { variant: 'good', icon: CircleCheck },
  REVOKED: { variant: 'muted', icon: Ban },
}

const STAFF_STATUS: Record<StaffStatus, Spec> = {
  ACTIVE: { variant: 'good', icon: CircleCheck },
  DISABLED: { variant: 'muted', icon: UserRoundX },
}

/** SANDBOX or PROD, always with an icon and a label (never color alone). */
export function EnvBadge({ env }: { env: TenantEnv }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = ENV[env]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`env.${env}`)}
    </Badge>
  )
}

export function TenantStatusBadge({ status }: { status: TenantStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = TENANT_STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`tenantStatus.${status}`)}
    </Badge>
  )
}

export function ApiKeyStatusBadge({ status }: { status: ApiKeyStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = API_KEY_STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`apiKeys.status.${status}`)}
    </Badge>
  )
}

export function StaffStatusBadge({ status }: { status: StaffStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STAFF_STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`staff.status.${status}`)}
    </Badge>
  )
}

/** An API key's scopes (`tenants:read`), as identifiers in monospace: they are not translated. */
export function ScopeBadges({ scopes }: { scopes: readonly string[] }) {
  return (
    <ul className="flex flex-wrap gap-1">
      {scopes.map((scope) => (
        <li key={scope}>
          <Badge variant="mono">{scope}</Badge>
        </li>
      ))}
    </ul>
  )
}

export function RoleBadge({ role }: { role: Role }) {
  const { t } = useTranslation()
  return (
    <Badge variant="muted">
      <UserRound aria-hidden />
      {t(`roles.${role}`)}
    </Badge>
  )
}

export function RoleBadges({ roles }: { roles: readonly Role[] }) {
  return (
    <ul className="flex flex-wrap gap-1">
      {roles.map((role) => (
        <li key={role}>
          <RoleBadge role={role} />
        </li>
      ))}
    </ul>
  )
}
