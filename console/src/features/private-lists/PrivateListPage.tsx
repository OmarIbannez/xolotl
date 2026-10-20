import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import type { PrivateListVersionResponse } from '@/api/types'
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
import { EntriesCard } from './EntriesCard'
import { usePrivateList, usePrivateListVersions } from './queries'
import { UploadCard } from './UploadCard'

/** A list's versions, newest first, with their counts. */
function HistoryCard({ listId }: { listId: string }) {
  const { t } = useTranslation()
  const format = useFormat()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const versions = usePrivateListVersions(listId, cursor)
  const page = versions.data

  const count = (value: number) => <span className="tabular">{format.number(value)}</span>
  const columns: Column<PrivateListVersionResponse>[] = [
    { id: 'seq', header: t('privateLists.history.seq'), cell: (row) => <span className="tabular">{row.seq}</span> },
    {
      id: 'status',
      header: t('privateLists.history.status'),
      cell: (row) => (
        <Badge variant={row.status === 'ACTIVE' ? 'good' : 'muted'}>{t(`lists.status.${row.status}`)}</Badge>
      ),
    },
    { id: 'uploadedAt', header: t('privateLists.history.uploadedAt'), cell: (row) => <DateTimeCell value={row.uploaded_at} /> },
    { id: 'entries', header: t('privateLists.history.entries'), cell: (row) => count(row.entry_count), className: 'text-right' },
    { id: 'added', header: t('lists.change.ADDED'), cell: (row) => count(row.added), className: 'text-right' },
    { id: 'changed', header: t('lists.change.CHANGED'), cell: (row) => count(row.changed), className: 'text-right' },
    { id: 'removed', header: t('lists.change.REMOVED'), cell: (row) => count(row.removed), className: 'text-right' },
    {
      id: 'uploadedBy',
      header: t('privateLists.history.uploadedBy'),
      cell: (row) => <span className="font-mono text-xs">{row.uploaded_by}</span>,
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('privateLists.history.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        {versions.isError ? (
          <ErrorBlock error={versions.error} onRetry={() => void versions.refetch()} />
        ) : (
          <div className={cn(versions.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <DataTable
              label={t('privateLists.history.label')}
              columns={columns}
              rows={page?.items}
              getRowId={(row) => row.id}
              loading={versions.isPending}
              empty={t('privateLists.history.empty')}
            />
          </div>
        )}
      </CardContent>
      {(cursor !== undefined || typeof page?.next_cursor === 'string') && (
        <CardFooter>
          <div className="w-full">
            <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={versions.isPlaceholderData} />
          </div>
        </CardFooter>
      )}
    </Card>
  )
}

/** One private list: upload a version with the template, its history, and its entries on reveal. */
export function PrivateListPage() {
  const { t } = useTranslation()
  const { listId = '' } = useParams()
  const { list } = usePrivateList(listId)

  return (
    <>
      <PageHeader
        title={list?.title ?? t('privateLists.detail.fallbackTitle')}
        description={list ? t('privateLists.detail.description', { code: list.list_code }) : undefined}
        actions={
          <Button asChild variant="outline">
            <Link to="/private-lists">
              <ArrowLeft aria-hidden />
              {t('privateLists.detail.back')}
            </Link>
          </Button>
        }
      />

      <div className="animate-fade-in-up space-y-6">
        <UploadCard listId={listId} />
        <HistoryCard listId={listId} />
        <EntriesCard key={listId} listId={listId} />
      </div>
    </>
  )
}
