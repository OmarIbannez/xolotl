import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useDocumentTitle } from '@/hooks/use-document-title'

interface MessagePageProps {
  icon: LucideIcon
  title: string
  children: ReactNode
  actions?: ReactNode
}

/** A centered notice for dead ends: no access, page not found, session could not load. */
export function MessagePage({ icon: Icon, title, children, actions }: MessagePageProps) {
  useDocumentTitle(title)
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 px-4 py-16 text-center">
      <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Icon className="size-5" aria-hidden />
      </span>
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="text-sm text-muted-foreground">{children}</div>
      {actions && <div className="mt-2 flex flex-wrap justify-center gap-2">{actions}</div>}
    </div>
  )
}
