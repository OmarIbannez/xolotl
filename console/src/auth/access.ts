import type { Role } from '@/api/types'
import { redirectTarget, type RedirectState } from './redirect'
import type { AuthStatus } from './use-auth'

/**
 * Who may open each admin area. The navigation and the route guards both read this
 * table, so a link is hidden exactly when its route is blocked. The API enforces the
 * same rules; this is about not offering what would only fail.
 */
export const ACCESS = {
  apiKeys: ['ADMIN'],
  staff: ['ADMIN'],
  audit: ['MLRO', 'ADMIN', 'AUDITOR_RO'],
} as const satisfies Record<string, readonly Role[]>

/** True when no role is required, or the user holds at least one of the required roles. */
export function hasAnyRole(userRoles: readonly Role[], required: readonly Role[]): boolean {
  return required.length === 0 || required.some((role) => userRoles.includes(role))
}

export type RouteDecision =
  /** The session is still being checked. */
  | { kind: 'wait' }
  /** A stored session could not be checked (network or server error). */
  | { kind: 'error' }
  | { kind: 'redirect'; to: string; state?: RedirectState }
  | { kind: 'allow' }

export interface RouteGuardInput {
  status: AuthStatus
  mustChangePassword: boolean
  /** How the previous session ended, if one did. */
  endedBy: 'signedOut' | 'expired' | null
  /** True for the route that exists to change the password (/setup-password). */
  allowPasswordChange: boolean
  /** The path being visited, with its query. */
  path: string
  /** The router state of the visited location (where the visitor was headed, if remembered). */
  state: unknown
}

/**
 * What `RequireAuth` does for a visit. Kept apart from the component so the routing
 * rules (and their absence of loops) can be tested without rendering.
 *
 * - Anonymous visitors go to /login remembering where they were headed; a visitor who
 *   just signed out starts afresh.
 * - An account that must change its password is held at /setup-password, and sent back
 *   where it was headed once it has.
 */
export function decideRoute(input: RouteGuardInput): RouteDecision {
  const { status, mustChangePassword, endedBy, allowPasswordChange, path, state } = input
  if (status === 'loading') return { kind: 'wait' }
  if (status === 'error') return { kind: 'error' }
  if (status === 'anonymous') {
    return endedBy === 'signedOut'
      ? { kind: 'redirect', to: '/login' }
      : { kind: 'redirect', to: '/login', state: { from: path } }
  }
  if (mustChangePassword && !allowPasswordChange) {
    return { kind: 'redirect', to: '/setup-password', state: { from: path } }
  }
  if (!mustChangePassword && allowPasswordChange) {
    return { kind: 'redirect', to: redirectTarget(state) }
  }
  return { kind: 'allow' }
}
