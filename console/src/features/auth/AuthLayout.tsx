import type { ReactNode } from 'react'
import { Logo } from '@/components/common/Logo'
import { LanguageSwitcher } from '@/components/layout/LanguageSwitcher'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'

interface AuthLayoutProps {
  title: string
  description: string
  children: ReactNode
  footer?: ReactNode
}

/** The frame shared by the pre-session screens: sign in, two-step code, new password. */
export function AuthLayout({ title, description, children, footer }: AuthLayoutProps) {
  return (
    <div className="flex min-h-dvh flex-col bg-surface-1">
      <div className="flex justify-end gap-1 p-4">
        <LanguageSwitcher />
        <ThemeToggle />
      </div>
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 pb-16">
        <Logo className="mb-6 justify-center" />
        <Card className="animate-fade-in-up">
          <CardHeader>
            <h1 className="font-heading text-lg leading-snug font-semibold">{title}</h1>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">{children}</CardContent>
        </Card>
        {footer && <div className="mt-4 text-center text-sm">{footer}</div>}
      </main>
    </div>
  )
}
