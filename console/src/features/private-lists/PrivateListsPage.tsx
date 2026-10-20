import { Plus, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { PrivateListResponse } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { useFormat } from '@/i18n/use-locale'
import { CreatePrivateListDialog } from './CreatePrivateListDialog'
import { usePrivateLists } from './queries'

/** True when the tenant has an LPB list with an active version. */
const hasActiveLpb = (lists: readonly PrivateListResponse[]) =>
  lists.some((list) => list.list_code === 'MX_LPB' && list.active_version !== null)

/** The tenant's private lists with their active versions, and the dialog that creates one. */
export function PrivateListsPage() {
  const { t } = useTranslation()
  const format = useFormat()
  const lists = usePrivateLists()
  const [creating, setCreating] = useState(false)

  const none = <span className="text-muted-foreground">{t('privateLists.table.noVersion')}</span>
  const dash = <span className="text-muted-foreground">—</span>
  const columns: Column<PrivateListResponse>[] = [
    {
      id: 'code',
      header: t('privateLists.table.code'),
      cell: (row) => (
        <Link to={`/private-lists/${row.id}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.list_code}
        </Link>
      ),
    },
    {
      id: 'title',
      header: t('privateLists.table.title'),
      cell: (row) => row.title,
      className: 'min-w-48 max-w-80 whitespace-normal break-words',
    },
    {
      id: 'kind',
      header: t('privateLists.table.kind'),
      cell: (row) => <Badge variant="muted">{t(`privateLists.kind.${row.list_kind}`)}</Badge>,
    },
    {
      id: 'version',
      header: t('privateLists.table.version'),
      cell: (row) => (row.active_version === null ? none : <span className="tabular">{row.active_version.seq}</span>),
    },
    {
      id: 'entries',
      header: t('privateLists.table.entries'),
      cell: (row) =>
        row.active_version === null ? dash : <span className="tabular">{format.number(row.active_version.entry_count)}</span>,
      className: 'text-right',
    },
    {
      id: 'uploadedAt',
      header: t('privateLists.table.uploadedAt'),
      cell: (row) => (row.active_version === null ? dash : <DateTimeCell value={row.active_version.uploaded_at} />),
    },
  ]

  return (
    <>
      <PageHeader
        title={t('privateLists.title')}
        description={t('privateLists.description')}
        actions={
          <Button type="button" onClick={() => setCreating(true)}>
            <Plus aria-hidden />
            {t('privateLists.create.button')}
          </Button>
        }
      />

      <div className="space-y-6">
        {lists.data && !hasActiveLpb(lists.data.items) && (
          <Alert role="status" className="animate-fade-in-up border-warning/40 bg-warning/10">
            <TriangleAlert aria-hidden className="text-warning" />
            <AlertDescription>{t('privateLists.lpbMissing')}</AlertDescription>
          </Alert>
        )}

        <Card className="animate-fade-in-up">
          <CardContent>
            {lists.isError ? (
              <ErrorBlock error={lists.error} onRetry={() => void lists.refetch()} />
            ) : (
              <DataTable
                label={t('privateLists.table.label')}
                columns={columns}
                rows={lists.data?.items}
                getRowId={(row) => row.id}
                loading={lists.isPending}
                empty={t('privateLists.empty')}
              />
            )}
          </CardContent>
        </Card>
      </div>

      {creating && <CreatePrivateListDialog onClose={() => setCreating(false)} />}
    </>
  )
}
