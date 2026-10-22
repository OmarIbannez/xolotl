import { BookOpen, CalendarDays, ChartNoAxesColumn, Coins, Contact, Handshake, House, KeyRound, ListChecks, Lock, Plug, ScanSearch, ScrollText, ShieldAlert, ShieldCheck, Users, type LucideIcon } from 'lucide-react'
import type { Role } from '@/api/types'
import { ACCESS } from '@/auth/access'

export interface NavItem {
  to: string
  labelKey:
    | 'nav.home'
    | 'nav.parties'
    | 'nav.matches'
    | 'nav.screeningCheck'
    | 'nav.counterparties'
    | 'nav.apiKeys'
    | 'nav.staff'
    | 'nav.audit'
    | 'nav.policy'
    | 'nav.lists'
    | 'nav.privateLists'
    | 'nav.screeningEvaluation'
    | 'nav.units'
    | 'nav.calendar'
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
  { to: '/parties', labelKey: 'nav.parties', icon: Contact, roles: ACCESS.parties },
  { to: '/screening/matches', labelKey: 'nav.matches', icon: ShieldAlert, roles: ACCESS.screening },
  { to: '/screening/check', labelKey: 'nav.screeningCheck', icon: ScanSearch, roles: ACCESS.screeningCheck },
  { to: '/counterparties', labelKey: 'nav.counterparties', icon: Handshake, roles: ACCESS.screening },
  { to: '/api-keys', labelKey: 'nav.apiKeys', icon: KeyRound, roles: ACCESS.apiKeys },
  { to: '/staff', labelKey: 'nav.staff', icon: Users, roles: ACCESS.staff },
  { to: '/audit', labelKey: 'nav.audit', icon: ScrollText, roles: ACCESS.audit },
  { to: '/policy', labelKey: 'nav.policy', icon: BookOpen },
  { to: '/lists', labelKey: 'nav.lists', icon: ListChecks, roles: ACCESS.lists },
  { to: '/private-lists', labelKey: 'nav.privateLists', icon: Lock, roles: ACCESS.privateLists },
  { to: '/screening/evaluation', labelKey: 'nav.screeningEvaluation', icon: ChartNoAxesColumn, roles: ACCESS.screening },
  { to: '/units', labelKey: 'nav.units', icon: Coins },
  { to: '/calendar', labelKey: 'nav.calendar', icon: CalendarDays },
  { to: '/connectors', labelKey: 'nav.connectors', icon: Plug, roles: ACCESS.connectors },
  { to: '/security', labelKey: 'nav.security', icon: ShieldCheck },
]
