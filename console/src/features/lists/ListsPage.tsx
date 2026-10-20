import { Lock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import type { ListOverview } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { useFormat } from '@/i18n/use-locale'
import { FreshnessBadge } from './FreshnessBadge'
import { useListOverview, useSetSubscription } from './queries'

/**
 * The lists the tenant is subscribed to: freshness, active version and counts, and for ADMIN
 * the switch of each recommended list. Mandatory lists are always on.
 */
export function ListsPage() {
  const { t } = useTranslation()
  const format = useFormat()
  const { hasRole } = useAuth()
  const overview = useListOverview()
  const toggle = useSetSubscription()
  const canToggle = hasRole(ACCESS.listsAdmin)

  function setEnabled(row: ListOverview, enabled: boolean) {
    toggle.mutate(
      { code: row.code, enabled },
      { onSuccess: () => toast.success(t('lists.toggled', { code: row.code })) },
    )
  }

  function subscription(row: ListOverview) {
    if (row.requirement === 'MANDATORY') {
      return (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Lock className="size-3.5" aria-hidden />
          {t('lists.mandatory')}
        </span>
      )
    }
    if (canToggle) {
      return (
        <Switch
          checked={row.enabled}
          aria-label={row.code}
          disabled={toggle.isPending}
          onCheckedChange={(enabled) => setEnabled(row, enabled)}
        />
      )
    }
    return row.enabled ? t('lists.subscription.on') : t('lists.subscription.off')
  }

  const none = <span className="text-muted-foreground">—</span>
  const columns: Column<ListOverview>[] = [
    {
      id: 'code',
      header: t('lists.table.code'),
      cell: (row) => (
        <Link to={`/lists/${row.code}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.code}
        </Link>
      ),
    },
    {
      id: 'title',
      header: t('lists.table.title'),
      cell: (row) => row.title,
      className: 'min-w-48 max-w-80 whitespace-normal break-words',
    },
    {
      id: 'requirement',
      header: t('lists.table.requirement'),
      cell: (row) => (
        <Badge variant={row.requirement === 'MANDATORY' ? 'info' : 'muted'}>
          {t(`lists.requirement.${row.requirement}`)}
        </Badge>
      ),
    },
    { id: 'freshness', header: t('lists.table.freshness'), cell: (row) => <FreshnessBadge row={row} /> },
    {
      id: 'version',
      header: t('lists.table.version'),
      cell: (row) => (row.active_seq === null ? none : <span className="tabular">{row.active_seq}</span>),
      className: 'text-right',
    },
    {
      id: 'entries',
      header: t('lists.table.entries'),
      cell: (row) => (row.entry_count === null ? none : <span className="tabular">{format.number(row.entry_count)}</span>),
      className: 'text-right',
    },
    {
      id: 'lastSuccess',
      header: t('lists.table.lastSuccess'),
      cell: (row) => (row.last_success_at === null ? none : <DateTimeCell value={row.last_success_at} />),
    },
    { id: 'subscription', header: t('lists.table.subscription'), cell: subscription },
  ]

  return (
    <>
      <PageHeader title={t('lists.title')} description={t('lists.description')} />

      <Card className="animate-fade-in-up">
        <CardContent className="space-y-4">
          {toggle.isError && <ErrorBlock error={toggle.error} />}
          {overview.isError ? (
            <ErrorBlock error={overview.error} onRetry={() => void overview.refetch()} />
          ) : (
            <DataTable
              label={t('lists.table.label')}
              columns={columns}
              rows={overview.data?.items}
              getRowId={(row) => row.code}
              loading={overview.isPending}
              empty={t('lists.empty')}
            />
          )}
        </CardContent>
      </Card>
    </>
  )
}
