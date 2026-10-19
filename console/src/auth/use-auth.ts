import { createContext, use } from 'react'
import type { Me, Role } from '@/api/types'
import type { SessionSnapshot } from './session'

export type AuthStatus =
  /** A stored token is being checked against GET /auth/me. */
  | 'loading'
  | 'anonymous'
  | 'authenticated'
  /** A token exists but the profile could not be loaded (network or server error). */
  | 'error'

export interface Credentials {
  tenantSlug: string
  email: string
  password: string
}

export type LoginOutcome =
  /** TOTP is enabled: ask for the code and call `loginTotp` with this token. */
  | { kind: 'mfa'; mfaToken: string }
  | { kind: 'signedIn'; me: Me }

export interface AuthValue {
  status: AuthStatus
  user: Me | undefined
  /** Why the profile could not be loaded, when `status` is 'error'. */
  error: unknown
  /** How the previous session ended, once one has: by sign-out or by the API's 401. */
  endedBy: SessionSnapshot['endedBy']
  /** The account must set a new password before anything else (spec section 6.1). */
  mustChangePassword: boolean
  login: (credentials: Credentials) => Promise<LoginOutcome>
  loginTotp: (input: { mfaToken: string; code: string }) => Promise<Me>
  logout: () => void
  /**
   * Change the password. On success the forced-change gate lifts immediately and, when the
   * API answers with a replacement token (it retires the one that made the request), that
   * token becomes the session's before any other call is made.
   */
  changePassword: (input: { currentPassword: string; newPassword: string }) => Promise<void>
  /** Reload the profile from the API (also how a failed load is retried). */
  refreshMe: () => Promise<Me>
  /** True when the user holds at least one of the roles (or none are required). */
  hasRole: (roles: readonly Role[]) => boolean
}

export const AuthContext = createContext<AuthValue | null>(null)

export function useAuth(): AuthValue {
  const ctx = use(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

/** The signed-in user, for components that only render behind `RequireAuth`. */
export function useUser(): Me {
  const { user } = useAuth()
  if (!user) throw new Error('useUser must be used behind RequireAuth')
  return user
}
