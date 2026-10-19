import { LogOut } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth, useUser } from '@/auth/use-auth'
import { RoleBadges } from '@/components/common/badges'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
  return letters.join('') || '?'
}

/** The signed-in user: name and roles in the header, details and sign-out in the menu. */
export function UserMenu() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  const user = useUser()
  const roleNames = user.roles.map((role) => t(`roles.${role}`)).join(' · ')
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="h-auto gap-2 py-1 pr-2.5 pl-1.5 max-md:gap-0 max-md:pr-1.5"
          title={t('header.accountMenu')}
        >
          <span
            aria-hidden
            className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground"
          >
            {initials(user.display_name)}
          </span>
          <span className="min-w-0 text-left leading-tight">
            <span className="block max-w-40 truncate text-sm font-medium max-md:sr-only">
              {user.display_name}
            </span>
            <span className="block max-w-40 truncate text-xs font-normal text-muted-foreground max-md:hidden">
              {roleNames}
            </span>
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="space-y-1.5">
          <div className="text-sm font-medium text-foreground">{user.display_name}</div>
          <div className="text-xs font-normal break-all text-muted-foreground">{user.email}</div>
          <RoleBadges roles={user.roles} />
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={logout}>
          <LogOut /> {t('header.signOut')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
