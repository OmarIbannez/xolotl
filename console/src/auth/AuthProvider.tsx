import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, unwrap } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Me, TokenResponse } from '@/api/types'
import { hasAnyRole } from './access'
import { session } from './session'
import { AuthContext, type AuthStatus, type AuthValue, type Credentials } from './use-auth'

const fetchMe = () => unwrap(api.GET('/auth/me'))

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const { t } = useTranslation()
  const { token, passwordChangeRequired, endedBy } = useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
  )

  // Keyed by token, so one session's profile can never be served to another.
  const me = useQuery({
    queryKey: ['me', token],
    queryFn: fetchMe,
    enabled: token !== null,
    retry: false,
    staleTime: 60_000,
  })

  // When the token goes away (sign-out, or a 401 anywhere), drop everything cached
  // for the old session; tell the user when the session ended on its own.
  const previousToken = useRef(token)
  useEffect(() => {
    if (previousToken.current !== null && token === null) {
      qc.clear()
      if (endedBy === 'expired') toast.info(t('auth.login.expired'))
    }
    previousToken.current = token
  }, [token, endedBy, qc, t])

  /** Adopt a new token and load its profile; a profile that fails to load is not a session. */
  const startSession = useCallback(
    async (response: TokenResponse): Promise<Me> => {
      session.start(response.access_token)
      try {
        return await qc.fetchQuery({
          queryKey: ['me', response.access_token],
          queryFn: fetchMe,
          staleTime: 0,
        })
      } catch (error) {
        session.end()
        throw error
      }
    },
    [qc],
  )

  const login = useCallback<AuthValue['login']>(
    async ({ tenantSlug, email, password }: Credentials) => {
      const result = await unwrap(
        api.POST('/auth/login', {
          body: { tenant_slug: tenantSlug, email, password },
        }),
      )
      if ('mfa_token' in result) return { kind: 'mfa', mfaToken: result.mfa_token }
      return { kind: 'signedIn', me: await startSession(result) }
    },
    [startSession],
  )

  const loginTotp = useCallback<AuthValue['loginTotp']>(
    async ({ mfaToken, code }) => {
      const result = await unwrap(
        api.POST('/auth/login/totp', { body: { mfa_token: mfaToken, code } }),
      )
      if ('mfa_token' in result) {
        // The second step must end in a token; another challenge is a server fault.
        throw new ApiError({ status: 500, code: 'INTERNAL', detail: 'unexpected MFA challenge' })
      }
      return startSession(result)
    },
    [startSession],
  )

  const logout = useCallback(() => {
    session.end()
  }, [])

  const refreshMe = useCallback(async (): Promise<Me> => {
    const fresh = await qc.fetchQuery({
      queryKey: ['me', session.getToken()],
      queryFn: fetchMe,
      staleTime: 0,
    })
    if (!fresh.must_change_password) session.clearPasswordChange()
    return fresh
  }, [qc])

  const changePassword = useCallback<AuthValue['changePassword']>(
    async ({ currentPassword, newPassword }) => {
      const tokenBefore = session.getToken()
      const { data } = await api.POST('/auth/password', {
        body: { current_password: currentPassword, new_password: newPassword },
      })
      // The session ended (sign-out, expiry) while the change was in flight. The password is
      // changed, but a token must not bring a finished session back.
      if (session.getToken() !== tokenBefore) return
      // The change ends every earlier session, this one included, and the answer carries the
      // token to continue with. Switch to it and lift the gate now (the profile is keyed by
      // token, so carry it over), then confirm with the server. An API that answers 204
      // instead keeps the current token good: only the gate lifts.
      const nextToken = data?.access_token
      if (nextToken) {
        const profile = qc.getQueryData<Me>(['me', tokenBefore])
        if (profile) {
          qc.setQueryData<Me>(['me', nextToken], { ...profile, must_change_password: false })
        }
        session.start(nextToken)
        qc.removeQueries({ queryKey: ['me', tokenBefore] })
      } else {
        qc.setQueryData<Me>(['me', tokenBefore], (old) =>
          old ? { ...old, must_change_password: false } : old,
        )
        session.clearPasswordChange()
      }
      void refreshMe().catch(() => undefined)
    },
    [qc, refreshMe],
  )

  const user = token === null ? undefined : me.data
  const mustChangePassword =
    user !== undefined && (user.must_change_password || passwordChangeRequired)

  let status: AuthStatus
  if (token === null) status = 'anonymous'
  else if (user !== undefined) status = 'authenticated'
  else if (me.isError) status = 'error'
  else status = 'loading'

  const value = useMemo<AuthValue>(
    () => ({
      status,
      user,
      error: me.error,
      endedBy,
      mustChangePassword,
      login,
      loginTotp,
      logout,
      changePassword,
      refreshMe,
      hasRole: (roles) => user !== undefined && hasAnyRole(user.roles, roles),
    }),
    [
      status,
      user,
      me.error,
      endedBy,
      mustChangePassword,
      login,
      loginTotp,
      logout,
      changePassword,
      refreshMe,
    ],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}
