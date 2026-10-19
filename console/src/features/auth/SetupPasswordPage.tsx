import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/use-auth'
import { Button } from '@/components/ui/button'
import { useDocumentTitle } from '@/hooks/use-document-title'
import { AuthLayout } from './AuthLayout'
import { PasswordChangeForm } from './PasswordChangeForm'

/**
 * Shown while the account still has its temporary password (`must_change_password`). Once
 * the change is accepted the gate lifts, and `RequireAuth` sends the user back to the page
 * they were headed for.
 */
export function SetupPasswordPage() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  useDocumentTitle(t('auth.setupPassword.title'))

  return (
    <AuthLayout
      title={t('auth.setupPassword.title')}
      description={t('auth.setupPassword.description')}
      footer={
        <Button variant="link" onClick={logout}>
          {t('auth.setupPassword.signOut')}
        </Button>
      }
    >
      <PasswordChangeForm autoFocus />
    </AuthLayout>
  )
}
