import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { DeltaChange, DeltaRowResponse, ListVersionResponse } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { EntrySheet } from './EntrySheet'
import { useListDelta } from './queries'

const CHANGES: readonly DeltaChange[] = ['ADDED', 'CHANGED', 'REMOVED']

const CHANGE_VARIANT: Record<DeltaChange, 'good' | 'warning' | 'critical'> = {
  ADDED: 'good',
  CHANGED: 'warning',
  REMOVED: 'critical',
}

interface DeltaTableProps {
  code: string
  version: ListVersionResponse
}

/**
 * What a version changed against the one before: one row per entry key, with the previous and
 * the new primary name side by side. The filter narrows the page on screen; a name opens its
 * entry. Mount it with `key={version.id}` so another version starts from its first page.
 */
export function DeltaTable({ code, version }: DeltaTableProps) {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [filter, setFilter] = useState<DeltaChange | null>(null)
  const [entryId, setEntryId] = useState<string | undefined>(undefined)
  const delta = useListDelta(code, version.id, cursor)
  const page = delta.data
  const rows = page?.items.filter((row) => filter === null || row.change === filter)

  const none = <span className="text-muted-foreground">—</span>
  const name = (text: string | null, id: string | null) =>
    text === null ? (
      none
    ) : id === null ? (
      text
    ) : (
      <button
        type="button"
        className="text-left underline-offset-4 hover:underline focus-visible:underline"
        onClick={() => setEntryId(id)}
      >
        {text}
      </button>
    )

  const columns: Column<DeltaRowResponse>[] = [
    {
      id: 'key',
      header: t('lists.delta.key'),
      cell: (row) => <span className="font-mono text-xs">{row.source_entry_key}</span>,
    },
    {
      id: 'change',
      header: t('lists.delta.change'),
      cell: (row) => <Badge variant={CHANGE_VARIANT[row.change]}>{t(`lists.change.${row.change}`)}</Badge>,
    },
    {
      id: 'previous',
      header: t('lists.delta.previous'),
      cell: (row) => name(row.prev_name, row.prev_entry_id),
      className: 'min-w-40 max-w-72 whitespace-normal break-words',
    },
    {
      id: 'new',
      header: t('lists.delta.new'),
      cell: (row) => name(row.new_name, row.new_entry_id),
      className: 'min-w-40 max-w-72 whitespace-normal break-words',
    },
  ]

  return (
    <div className="space-y-4">
      <div role="group" aria-label={t('lists.delta.filter')} className="flex flex-wrap gap-2">
        {CHANGES.map((change) => (
          <Button
            key={change}
            type="button"
            size="sm"
            variant={filter === change ? 'default' : 'outline'}
            aria-pressed={filter === change}
            onClick={() => setFilter(filter === change ? null : change)}
          >
            {t(`lists.change.${change}`)}
          </Button>
        ))}
      </div>

      {delta.isError ? (
        <ErrorBlock error={delta.error} onRetry={() => void delta.refetch()} />
      ) : (
        <div className={cn(delta.isPlaceholderData && 'opacity-60 transition-opacity')}>
          <DataTable
            label={t('lists.delta.label')}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.source_entry_key}
            loading={delta.isPending}
            empty={filter === null ? t('lists.delta.empty') : t('lists.delta.emptyFiltered')}
          />
        </div>
      )}

      {(cursor !== undefined || typeof page?.next_cursor === 'string') && (
        <CursorPager
          cursor={cursor}
          nextCursor={page?.next_cursor}
          onChange={setCursor}
          busy={delta.isPlaceholderData}
        />
      )}

      <Sheet open={entryId !== undefined} onOpenChange={(open) => !open && setEntryId(undefined)}>
        {entryId !== undefined && <EntrySheet code={code} entryId={entryId} />}
      </Sheet>
    </div>
  )
}
