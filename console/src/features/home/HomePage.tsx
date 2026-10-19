import { useTranslation } from 'react-i18next'
import { useTenant } from '@/api/queries'
import { useUser } from '@/auth/use-auth'
import { EnvBadge, RoleBadges, TenantStatusBadge } from '@/components/common/badges'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'

export function HomePage() {
  const { t } = useTranslation()
  const user = useUser()
  const tenant = useTenant()
  const format = useFormat()

  return (
    <>
      <PageHeader title={t('home.title')} description={t('home.welcome', { name: user.display_name })} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="animate-fade-in-up">
          <CardHeader>
            <CardTitle>{t('home.tenant.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            {tenant.isError ? (
              <ErrorBlock error={tenant.error} />
            ) : tenant.data ? (
              <KeyValues
                items={[
                  [t('home.tenant.legalName'), tenant.data.legal_name],
                  [t('home.tenant.slug'), <span className="font-mono">{tenant.data.slug}</span>],
                  [
                    t('home.tenant.country'),
                    `${format.region(tenant.data.country_code)} (${tenant.data.country_code})`,
                  ],
                  [t('home.tenant.cell'), <span className="font-mono">{tenant.data.cell}</span>],
                  [t('home.tenant.env'), <EnvBadge env={tenant.data.env} />],
                  [t('home.tenant.status'), <TenantStatusBadge status={tenant.data.status} />],
                  [t('home.tenant.createdAt'), format.date(tenant.data.created_at)],
                ]}
              />
            ) : (
              <LoadingBlock rows={5} />
            )}
          </CardContent>
        </Card>

        <Card className="animate-fade-in-up stagger-1">
          <CardHeader>
            <CardTitle>{t('home.access.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            <KeyValues
              items={[
                [t('home.access.name'), user.display_name],
                [t('home.access.email'), user.email],
                [t('home.access.roles'), <RoleBadges roles={user.roles} />],
                [
                  t('home.access.totp'),
                  <Badge variant={user.totp_enabled ? 'good' : 'muted'}>
                    {user.totp_enabled ? t('home.access.totpOn') : t('home.access.totpOff')}
                  </Badge>,
                ],
              ]}
            />
          </CardContent>
        </Card>
      </div>
    </>
  )
}
