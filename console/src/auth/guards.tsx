import { ServerCrash } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import type { Role } from '@/api/types'
import { Forbidden } from '@/components/common/Forbidden'
import { FullPageLoading } from '@/components/common/FullPageLoading'
import { MessagePage } from '@/components/common/MessagePage'
import { Button } from '@/components/ui/button'
import { describeError } from '@/i18n/errors'
import { decideRoute } from './access'
import { pathOf } from './redirect'
import { useAuth } from './use-auth'

/** The stored session exists but its profile could not be loaded (the API is down, say). */
function SessionLoadError() {
  const { t } = useTranslation()
  const { error, refreshMe, logout } = useAuth()
  const { message, reference } = describeError(t, error)
  return (
    <MessagePage
      icon={ServerCrash}
      title={t('boot.errorTitle')}
      actions={
        <>
          <Button onClick={() => void refreshMe().catch(() => undefined)}>{t('common.retry')}</Button>
          <Button variant="outline" onClick={logout}>
            {t('header.signOut')}
          </Button>
        </>
      }
    >
      <p>{message}</p>
      {reference && (
        <p className="mt-1 font-mono text-xs">{t('errors.reference', { id: reference })}</p>
      )}
    </MessagePage>
  )
}

/**
 * Route guard for signed-in staff; the rules live in `decideRoute`. Pass
 * `allowPasswordChange` for the routes that exist for the forced password change.
 */
export function RequireAuth({ allowPasswordChange = false }: { allowPasswordChange?: boolean }) {
  const { status, mustChangePassword, endedBy } = useAuth()
  const location = useLocation()
  const decision = decideRoute({
    status,
    mustChangePassword,
    endedBy,
    allowPasswordChange,
    path: pathOf(location),
    state: location.state,
  })
  switch (decision.kind) {
    case 'wait':
      return <FullPageLoading />
    case 'error':
      return <SessionLoadError />
    case 'redirect':
      return <Navigate to={decision.to} replace state={decision.state} />
    case 'allow':
      return <Outlet />
  }
}

/** Route guard: renders the nested routes only for users holding one of `roles`. */
export function RequireRole({ roles }: { roles: readonly Role[] }) {
  const { hasRole } = useAuth()
  return hasRole(roles) ? <Outlet /> : <Forbidden />
}
