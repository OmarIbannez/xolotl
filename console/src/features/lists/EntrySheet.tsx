import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { useListEntry } from './queries'

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)
const arrayOf = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const textOf = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)
const strings = (value: unknown): string[] => arrayOf(value).flatMap((item) => textOf(item) ?? [])

/** A name's full text (`EntryName.full`). */
const nameOf = (value: unknown): string | null => (isObject(value) ? textOf(value.full) : null)

/** A `PartialDate` to the precision it is known: `1970`, `1970-03` or `1970-03-04`. */
function partialDate(value: unknown): string | null {
  if (!isObject(value) || typeof value.year !== 'number') return null
  const pad = (n: number) => String(n).padStart(2, '0')
  const parts = [String(value.year)]
  if (typeof value.month === 'number') parts.push(pad(value.month))
  if (typeof value.month === 'number' && typeof value.day === 'number') parts.push(pad(value.day))
  return parts.join('-')
}

function identifier(value: unknown): string | null {
  if (!isObject(value)) return null
  const shown = textOf(value.value)
  if (shown === null) return null
  const country = textOf(value.country)
  return `${textOf(value.id_type) ?? ''} ${shown}${country ? ` (${country})` : ''}`.trim()
}

/** One list entry's public data, read from its canonical JSON. Every value renders as text. */
function EntryDetail({ entry }: { entry: Json }) {
  const { t } = useTranslation()
  const none = <span className="text-muted-foreground">—</span>
  const joined = (values: (string | null)[]): ReactNode => {
    const present = values.filter((value): value is string => value !== null)
    return present.length === 0 ? none : present.join(', ')
  }
  const listed = (values: (string | null)[]): ReactNode => {
    const present = values.filter((value): value is string => value !== null)
    return present.length === 0 ? (
      none
    ) : (
      <ul className="space-y-0.5">
        {present.map((value, index) => (
          <li key={index}>{value}</li>
        ))}
      </ul>
    )
  }

  return (
    <KeyValues
      items={[
        [t('lists.entry.name'), nameOf(entry.primary_name) ?? none],
        [t('lists.entry.entityType'), textOf(entry.entity_type) ?? none],
        [t('lists.entry.aliases'), listed(arrayOf(entry.aliases).map(nameOf))],
        [t('lists.entry.birthDates'), joined(arrayOf(entry.birth_dates).map(partialDate))],
        [t('lists.entry.birthPlaces'), joined(strings(entry.birth_places))],
        [t('lists.entry.nationalities'), joined(strings(entry.nationalities))],
        [t('lists.entry.programs'), joined(strings(entry.programs))],
        [t('lists.entry.identifiers'), listed(arrayOf(entry.identifiers).map(identifier))],
        [t('lists.entry.listedOn'), textOf(entry.listed_on) ?? none],
      ]}
    />
  )
}

/**
 * The side panel with one entry of a shared list. Render it inside the `Sheet` that opened it;
 * it mounts only while open and loads the entry then.
 */
export function EntrySheet({ code, entryId }: { code: string; entryId: string }) {
  const { t } = useTranslation()
  const entry = useListEntry(code, entryId)

  return (
    <SheetContent side="right" className="w-[36rem] max-w-[92vw] overflow-y-auto">
      <div className="space-y-1 pr-8">
        <SheetTitle>{t('lists.entry.title')}</SheetTitle>
        <SheetDescription className="font-mono text-xs">
          {entry.data ? `${entry.data.list_code} · ${entry.data.source_entry_key}` : code}
        </SheetDescription>
      </div>
      {entry.isError ? (
        <ErrorBlock error={entry.error} onRetry={() => void entry.refetch()} />
      ) : entry.data === undefined ? (
        <LoadingBlock />
      ) : (
        <EntryDetail entry={entry.data.entry} />
      )}
    </SheetContent>
  )
}
