import { ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'
import { useAuth } from '@/auth/use-auth'
import { cn } from '@/lib/utils'
import { NAV_ITEMS } from './nav'

const linkClass = 'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors'

/**
 * The console's navigation. Links the user's roles do not allow are not rendered; the
 * routes behind them are blocked by `RequireRole` regardless.
 */
export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  return (
    <nav aria-label={t('nav.label')} className="flex flex-col gap-1">
      {NAV_ITEMS.filter((item) => hasRole(item.roles ?? [])).map(({ to, labelKey, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              linkClass,
              'text-muted-foreground hover:bg-muted hover:text-foreground',
              isActive && 'bg-accent font-medium text-accent-foreground hover:bg-accent',
            )
          }
        >
          <Icon className="size-4" aria-hidden />
          {t(labelKey)}
        </NavLink>
      ))}
      <a
        href="/v1/docs"
        target="_blank"
        rel="noreferrer"
        className={cn(linkClass, 'mt-3 text-muted-foreground hover:bg-muted hover:text-foreground')}
      >
        <ExternalLink className="size-4" aria-hidden />
        {t('nav.apiDocs')}
      </a>
    </nav>
  )
}
