import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CursorPager } from '@/components/common/CursorPager'
import { ErrorBlock } from '@/components/common/states'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { HitsTable } from './HitsTable'
import { usePartyHits } from './queries'

/** Cliente › Hallazgos de reglas: the party's hits, newest first, a page at a time. */
export function PartyHitsCard({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const hits = usePartyHits(partyId, cursor)
  const page = hits.data
  const title = t('hits.party.title')
  const showPager = cursor !== undefined || (page?.next_cursor ?? null) !== null
  return (
    <Card role="region" aria-label={title} className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {hits.isError ? (
          <ErrorBlock error={hits.error} onRetry={() => void hits.refetch()} />
        ) : (
          <div className={cn(hits.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <HitsTable label={t('hits.party.label')} rows={page?.items} empty={t('hits.party.empty')} showParty={false} />
          </div>
        )}
      </CardContent>
      {showPager && (
        <CardFooter>
          <div className="w-full">
            <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={hits.isPlaceholderData} />
          </div>
        </CardFooter>
      )}
    </Card>
  )
}
