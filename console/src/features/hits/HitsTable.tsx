import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { HitSummary, PartyResponse } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { useFormat } from '@/i18n/use-locale'
import { MexicoCityTimeCell } from '@/features/transactions/MexicoCityTimeCell'
import { mexicoCityDate, mexicoCityDateTime } from '@/features/transactions/time'
import { OutcomeBadge, SeverityBadge, TypologyTags } from './SeverityBadge'

/** The party of a hit, masked: its initials link to the party, the masked identifiers beneath. */
export function MaskedPartyCell({ party }: { party: PartyResponse }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-0.5">
      <Link to={`/parties/${party.id}`} title={party.id} className="font-medium underline-offset-4 hover:underline">
        {party.display_name ?? party.id.slice(0, 8)}
      </Link>
      {party.identifiers.length > 0 && (
        <div className="flex flex-wrap gap-x-2 font-mono text-xs text-muted-foreground">
          {party.identifiers.map((identifier) => (
            <span key={identifier.id}>
              <span className="sr-only">{t(`parties.identifierType.${identifier.id_type}`)} </span>
              {identifier.masked_value}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/** A hit's window as two Mexico City dates; the exact instants are in the tooltip. */
export function WindowCell({ start, end }: { start: string; end: string }) {
  const { locale } = useFormat()
  return (
    <span title={`${mexicoCityDateTime(start, locale)} – ${mexicoCityDateTime(end, locale)}`} className="whitespace-nowrap">
      {mexicoCityDate(start, locale)} – {mexicoCityDate(end, locale)}
    </span>
  )
}

interface HitsTableProps {
  label: string
  rows: readonly HitSummary[] | undefined
  loading?: boolean
  empty: string
  /** False on a party's own page, where every row is the same party. */
  showParty?: boolean
}

/** Hits as rows: the rule code opens the hit; party masked, severity, outcome, window, count, created. */
export function HitsTable({ label, rows, loading = false, empty, showParty = true }: HitsTableProps) {
  const { t } = useTranslation()
  const columns: Column<HitSummary>[] = [
    {
      id: 'rule',
      header: t('hits.table.rule'),
      cell: (row) => (
        <Link to={`/monitoring/hits/${row.id}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.rule_code}
        </Link>
      ),
    },
    ...(showParty
      ? [{ id: 'party', header: t('hits.table.party'), cell: (row: HitSummary) => <MaskedPartyCell party={row.party} /> }]
      : []),
    { id: 'typology', header: t('hits.table.typology'), cell: (row) => <TypologyTags typology={row.typology} /> },
    { id: 'severity', header: t('hits.table.severity'), cell: (row) => <SeverityBadge severity={row.severity} /> },
    { id: 'outcome', header: t('hits.table.outcome'), cell: (row) => <OutcomeBadge outcome={row.outcome} /> },
    { id: 'window', header: t('hits.table.window'), cell: (row) => <WindowCell start={row.window_start} end={row.window_end} /> },
    {
      id: 'firedCount',
      header: t('hits.table.firedCount'),
      cell: (row) => <span className="tabular">{row.fired_count}</span>,
      className: 'text-right',
    },
    { id: 'createdAt', header: t('hits.table.createdAt'), cell: (row) => <MexicoCityTimeCell value={row.created_at} /> },
  ]
  return (
    <DataTable label={label} columns={columns} rows={rows} getRowId={(row) => row.id} loading={loading} empty={empty} />
  )
}
