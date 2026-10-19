import { Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTenant } from '@/api/queries'
import { useUser } from '@/auth/use-auth'
import { EnvBadge } from '@/components/common/badges'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * The tenant the console is acting on: legal name, slug and environment. The slug is
 * known from the session right away; the rest arrives with GET /tenants/me.
 */
export function TenantSummary() {
  const { t } = useTranslation()
  const user = useUser()
  const tenant = useTenant()
  return (
    <section aria-label={t('header.tenant')} className="flex min-w-0 items-center gap-2.5">
      <Building2 className="hidden size-4 shrink-0 text-muted-foreground sm:block" aria-hidden />
      <div className="min-w-0 leading-tight">
        {tenant.data ? (
          <div className="truncate text-sm font-medium">{tenant.data.legal_name}</div>
        ) : tenant.isError ? null : (
          <Skeleton className="mb-1 h-4 w-36" />
        )}
        <div className="truncate font-mono text-xs text-muted-foreground">{user.tenant_slug}</div>
      </div>
      {tenant.data && <EnvBadge env={tenant.data.env} />}
    </section>
  )
}
