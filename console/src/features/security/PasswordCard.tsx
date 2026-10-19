import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PasswordChangeForm } from '@/features/auth/PasswordChangeForm'

/** Change the password: the same form the forced first-login change uses. */
export function PasswordCard() {
  const { t } = useTranslation()
  return (
    <Card className="animate-fade-in-up stagger-1">
      <CardHeader>
        <CardTitle role="heading" aria-level={2}>
          {t('security.password.title')}
        </CardTitle>
        <CardDescription>{t('security.password.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="max-w-md">
          <PasswordChangeForm />
        </div>
      </CardContent>
    </Card>
  )
}
