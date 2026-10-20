import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import type { ListVersionResponse } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { DeltaTable } from './DeltaTable'
import { useListVersions } from './queries'

/** One shared list's versions, newest first, and the delta of the version selected. */
export function ListVersionsPage() {
  const { t } = useTranslation()
  const format = useFormat()
  const { code = '' } = useParams()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [selected, setSelected] = useState<ListVersionResponse | undefined>(undefined)
  const versions = useListVersions(code, cursor)
  const page = versions.data

  const count = (value: number) => <span className="tabular">{format.number(value)}</span>
  const none = <span className="text-muted-foreground">—</span>
  const columns: Column<ListVersionResponse>[] = [
    { id: 'seq', header: t('lists.versions.seq'), cell: (row) => <span className="tabular">{row.seq}</span> },
    {
      id: 'status',
      header: t('lists.versions.status'),
      cell: (row) => (
        <Badge variant={row.status === 'ACTIVE' ? 'good' : 'muted'}>{t(`lists.status.${row.status}`)}</Badge>
      ),
    },
    {
      id: 'published',
      header: t('lists.versions.published'),
      cell: (row) => (row.published_at === null ? none : <DateTimeCell value={row.published_at} />),
    },
    { id: 'fetched', header: t('lists.versions.fetched'), cell: (row) => <DateTimeCell value={row.fetched_at} /> },
    { id: 'entries', header: t('lists.versions.entries'), cell: (row) => count(row.entry_count), className: 'text-right' },
    { id: 'added', header: t('lists.change.ADDED'), cell: (row) => count(row.added), className: 'text-right' },
    { id: 'changed', header: t('lists.change.CHANGED'), cell: (row) => count(row.changed), className: 'text-right' },
    { id: 'removed', header: t('lists.change.REMOVED'), cell: (row) => count(row.removed), className: 'text-right' },
    {
      id: 'loadedBy',
      header: t('lists.versions.loadedBy'),
      cell: (row) => <span className="font-mono text-xs">{row.loaded_by}</span>,
    },
    {
      id: 'show',
      header: <span className="sr-only">{t('lists.versions.showHeader')}</span>,
      cell: (row) => (
        <Button
          type="button"
          size="sm"
          variant={selected?.id === row.id ? 'default' : 'outline'}
          aria-label={t('lists.versions.show', { seq: row.seq })}
          aria-pressed={selected?.id === row.id}
          onClick={() => setSelected(row)}
        >
          {t('lists.versions.showShort')}
        </Button>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title={t('lists.versions.title', { code })}
        description={t('lists.versions.description')}
        actions={
          <Button asChild variant="outline">
            <Link to="/lists">
              <ArrowLeft aria-hidden />
              {t('lists.versions.back')}
            </Link>
          </Button>
        }
      />

      <div className="space-y-6">
        <Card className="animate-fade-in-up">
          <CardContent>
            {versions.isError ? (
              <ErrorBlock error={versions.error} onRetry={() => void versions.refetch()} />
            ) : (
              <div className={cn(versions.isPlaceholderData && 'opacity-60 transition-opacity')}>
                <DataTable
                  label={t('lists.versions.label')}
                  columns={columns}
                  rows={page?.items}
                  getRowId={(row) => row.id}
                  loading={versions.isPending}
                  empty={t('lists.versions.empty')}
                />
              </div>
            )}
          </CardContent>
          {(cursor !== undefined || typeof page?.next_cursor === 'string') && (
            <CardFooter>
              <div className="w-full">
                <CursorPager
                  cursor={cursor}
                  nextCursor={page?.next_cursor}
                  onChange={setCursor}
                  busy={versions.isPlaceholderData}
                />
              </div>
            </CardFooter>
          )}
        </Card>

        {selected && (
          <Card className="animate-fade-in-up">
            <CardHeader>
              <CardTitle>{t('lists.delta.title', { seq: selected.seq })}</CardTitle>
            </CardHeader>
            <CardContent>
              <DeltaTable key={selected.id} code={code} version={selected} />
            </CardContent>
          </Card>
        )}
      </div>
    </>
  )
}
