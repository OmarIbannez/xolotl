import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PrivateListEntryResponse } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { arrayOf, entityType, identifier, nameOf } from '@/features/lists/entry-json'
import { useRevealEntries } from './queries'

/**
 * The active version's entries, decrypted. Nothing is requested until the user presses
 * "Revelar"; each page then shown is one audited view (`VIEW_PII` and
 * `private_list.entries_viewed`), and "Ocultar" lets go of what was shown.
 */
export function EntriesCard({ listId }: { listId: string }) {
  const { t } = useTranslation()
  const reveal = useRevealEntries(listId)
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [shown, setShown] = useState(false)
  const page = reveal.data

  function show(next: string | undefined) {
    setShown(true)
    setCursor(next)
    reveal.mutate(next)
  }

  function hide() {
    setShown(false)
    setCursor(undefined)
    reveal.reset()
  }

  const none = <span className="text-muted-foreground">—</span>
  const columns: Column<PrivateListEntryResponse>[] = [
    {
      id: 'key',
      header: t('privateLists.entries.key'),
      cell: (row) => <span className="font-mono text-xs">{row.source_entry_key}</span>,
    },
    { id: 'type', header: t('privateLists.entries.type'), cell: (row) => entityType(t, row.entity_type) ?? none },
    {
      id: 'name',
      header: t('privateLists.entries.name'),
      cell: (row) => nameOf(row.entry.primary_name) ?? none,
      className: 'min-w-48 max-w-80 whitespace-normal break-words',
    },
    {
      id: 'identifiers',
      header: t('privateLists.entries.identifiers'),
      cell: (row) => {
        const ids = arrayOf(row.entry.identifiers).flatMap((value) => identifier(t, value) ?? [])
        return ids.length === 0 ? (
          none
        ) : (
          <ul className="space-y-0.5 font-mono text-xs">
            {ids.map((id, index) => (
              <li key={index}>{id}</li>
            ))}
          </ul>
        )
      },
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('privateLists.entries.title')}</CardTitle>
        <CardDescription>{t('privateLists.entries.description')}</CardDescription>
        <CardAction>
          {shown ? (
            <Button type="button" variant="outline" size="sm" onClick={hide}>
              <EyeOff aria-hidden />
              {t('privateLists.entries.hide')}
            </Button>
          ) : (
            <Button type="button" variant="outline" size="sm" onClick={() => show(undefined)}>
              <Eye aria-hidden />
              {t('privateLists.entries.reveal')}
            </Button>
          )}
        </CardAction>
      </CardHeader>
      {shown && (
        <CardContent className="space-y-3">
          {reveal.isError ? (
            <ErrorBlock error={reveal.error} onRetry={() => show(cursor)} />
          ) : (
            <>
              {page && (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <ShieldCheck className="size-3.5" aria-hidden />
                  {t('privateLists.entries.notice')}
                </p>
              )}
              <DataTable
                label={t('privateLists.entries.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(row) => row.id}
                loading={reveal.isPending}
                empty={t('privateLists.entries.empty')}
              />
            </>
          )}
        </CardContent>
      )}
      {shown && (cursor !== undefined || typeof page?.next_cursor === 'string') && (
        <CardFooter>
          <div className="w-full">
            <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={show} busy={reveal.isPending} />
          </div>
        </CardFooter>
      )}
    </Card>
  )
}
