import { useTranslation } from 'react-i18next'
import { NavLink, Outlet } from 'react-router-dom'
import { Logo } from '@/components/common/Logo'
import { LanguageSwitcher } from './LanguageSwitcher'
import { MobileNav } from './MobileNav'
import { SidebarNav } from './SidebarNav'
import { TenantSummary } from './TenantSummary'
import { ThemeToggle } from './ThemeToggle'
import { UserMenu } from './UserMenu'

/**
 * The signed-in frame: a sidebar (a sheet on small screens), a header naming the
 * tenant and the user, and the page itself. Render it behind `RequireAuth`.
 */
export function AppShell() {
  const { t } = useTranslation()
  return (
    <div className="min-h-dvh bg-background lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-background px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {t('app.skipToContent')}
      </a>

      <aside className="hidden border-r bg-surface-1 lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col lg:gap-6 lg:p-4">
        <NavLink to="/" className="flex items-baseline gap-2 px-2 pt-1">
          <Logo />
          <span className="text-xs text-muted-foreground">{t('app.console')}</span>
        </NavLink>
        <SidebarNav />
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/85 px-4 backdrop-blur md:gap-3 md:px-6">
          <MobileNav />
          <div className="min-w-0 flex-1">
            <TenantSummary />
          </div>
          <LanguageSwitcher />
          <ThemeToggle />
          <UserMenu />
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 outline-none md:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
