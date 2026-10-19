import type { ReactNode } from 'react'
import { useDocumentTitle } from '@/hooks/use-document-title'

interface PageHeaderProps {
  title: string
  description?: ReactNode
  actions?: ReactNode
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  useDocumentTitle(title)
  return (
    <header className="flex flex-col gap-3 pb-5 md:flex-row md:items-end md:justify-between">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{title}</h1>
        {description && <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

export function KeyValues({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-sm">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
