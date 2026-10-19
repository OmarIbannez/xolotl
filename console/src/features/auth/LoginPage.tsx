import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation } from 'react-router-dom'
import { redirectTarget, type RedirectState } from '@/auth/redirect'
import { useAuth } from '@/auth/use-auth'
import { Button } from '@/components/ui/button'
import { useDocumentTitle } from '@/hooks/use-document-title'
import { AuthLayout } from './AuthLayout'
import { CredentialsForm } from './CredentialsForm'
import { TotpForm } from './TotpForm'

/**
 * Workspace, email and password; then, when the account has TOTP, the code. One page
 * with two steps (no second route), so the MFA token never reaches the URL.
 */
export function LoginPage() {
  const { t } = useTranslation()
  const { status, mustChangePassword } = useAuth()
  const location = useLocation()
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  useDocumentTitle(mfaToken === null ? t('auth.login.title') : t('auth.totp.title'))

  if (status === 'authenticated') {
    // Signed in: carry on to where the visitor was headed, after the forced password change if due.
    const target = redirectTarget(location.state)
    if (!mustChangePassword) return <Navigate to={target} replace />
    const state: RedirectState = { from: target }
    return <Navigate to="/setup-password" replace state={state} />
  }

  if (mfaToken !== null) {
    return (
      <AuthLayout
        title={t('auth.totp.title')}
        description={t('auth.totp.description')}
        footer={
          <Button variant="link" onClick={() => setMfaToken(null)}>
            {t('auth.totp.back')}
          </Button>
        }
      >
        <TotpForm mfaToken={mfaToken} />
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title={t('auth.login.title')} description={t('auth.login.description')}>
      <CredentialsForm onMfa={setMfaToken} />
    </AuthLayout>
  )
}
