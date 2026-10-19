import { House, KeyRound, Plug, ScrollText, ShieldCheck, Users, type LucideIcon } from 'lucide-react'
import type { Role } from '@/api/types'
import { ACCESS } from '@/auth/access'

export interface NavItem {
  to: string
  labelKey:
    | 'nav.home'
    | 'nav.apiKeys'
    | 'nav.staff'
    | 'nav.audit'
    | 'nav.connectors'
    | 'nav.security'
  icon: LucideIcon
  /** Match the path exactly (for "/"). */
  end?: boolean
  /** Hidden from users holding none of these roles. Omit for everyone. */
  roles?: readonly Role[]
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', labelKey: 'nav.home', icon: House, end: true },
  { to: '/api-keys', labelKey: 'nav.apiKeys', icon: KeyRound, roles: ACCESS.apiKeys },
  { to: '/staff', labelKey: 'nav.staff', icon: Users, roles: ACCESS.staff },
  { to: '/audit', labelKey: 'nav.audit', icon: ScrollText, roles: ACCESS.audit },
  { to: '/connectors', labelKey: 'nav.connectors', icon: Plug, roles: ACCESS.connectors },
  { to: '/security', labelKey: 'nav.security', icon: ShieldCheck },
]
