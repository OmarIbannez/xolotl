import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

interface CursorPagerProps {
  /** The cursor of the page on screen; undefined for the first page. */
  cursor: string | undefined
  /** The `next_cursor` of the page on screen; absent or null on the last page. */
  nextCursor: string | null | undefined
  /** Asks for another page. The caller keeps `cursor` and fetches that page. */
  onChange: (cursor: string | undefined) => void
  /** A page is loading: both buttons wait, so a click cannot skip a page. */
  busy?: boolean
}

interface Trail {
  /** The cursors of the pages before the one on screen, oldest first (undefined = first page). */
  before: readonly (string | undefined)[]
  /** The cursor this trail leads to. */
  at: string | undefined
}

/**
 * Previous and next for a keyset-paginated list. The API only gives a cursor for the next page,
 * so the pager remembers the cursors it moved through (the back stack) in its own state, and
 * Previous returns along them.
 *
 * The stack is only valid while `cursor` is where the pager left it. If the caller moves
 * elsewhere (back to the first page after a create, say), the stack no longer applies and the
 * pager starts over from there.
 */
export function CursorPager({ cursor, nextCursor, onChange, busy = false }: CursorPagerProps) {
  const { t } = useTranslation()
  const [trail, setTrail] = useState<Trail>({ before: [], at: undefined })
  const before = trail.at === cursor ? trail.before : []
  const hasPrevious = before.length > 0
  const hasNext = typeof nextCursor === 'string'

  function goNext() {
    if (typeof nextCursor !== 'string') return
    setTrail({ before: [...before, cursor], at: nextCursor })
    onChange(nextCursor)
  }

  function goPrevious() {
    if (!hasPrevious) return
    const previous = before[before.length - 1]
    setTrail({ before: before.slice(0, -1), at: previous })
    onChange(previous)
  }

  return (
    <nav aria-label={t('common.pager.label')} className="flex items-center justify-between gap-3">
      <p aria-live="polite" className="tabular text-sm text-muted-foreground">
        {t('common.pager.page', { page: before.length + 1 })}
      </p>
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" onClick={goPrevious} disabled={!hasPrevious || busy}>
          <ChevronLeft aria-hidden />
          {t('common.pager.previous')}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={goNext} disabled={!hasNext || busy}>
          {t('common.pager.next')}
          <ChevronRight aria-hidden />
        </Button>
      </div>
    </nav>
  )
}
