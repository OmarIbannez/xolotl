import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { HitView } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { BandBadge } from './BandBadge'

/**
 * The hits of one check, as staff see them: band, list and score, with a link to the finding
 * when the hit opened or refreshed one (`match_id`). The explanation stays on the finding's page.
 */
export function HitList({ hits, empty }: { hits: readonly HitView[]; empty: string }) {
  const { t } = useTranslation()
  const columns: Column<HitView>[] = [
    { id: 'band', header: t('screening.hits.band'), cell: (hit) => <BandBadge band={hit.band} /> },
    { id: 'list', header: t('screening.hits.list'), cell: (hit) => <span className="font-mono text-xs">{hit.list_code}</span> },
    {
      id: 'score',
      header: t('screening.hits.score'),
      cell: (hit) => <span className="tabular">{hit.score}</span>,
      className: 'text-right',
    },
    {
      id: 'finding',
      header: t('screening.hits.finding'),
      cell: (hit) =>
        hit.match_id === null ? (
          <span className="text-muted-foreground">{t('screening.hits.noFinding')}</span>
        ) : (
          <Link to={`/screening/matches/${hit.match_id}`} className="underline-offset-4 hover:underline">
            {t('screening.hits.openFinding')}
          </Link>
        ),
    },
  ]
  return <DataTable label={t('screening.hits.label')} columns={columns} rows={hits} getRowId={(hit) => hit.id} empty={empty} />
}
