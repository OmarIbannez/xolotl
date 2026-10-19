import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

interface CursorNavProps {
  /** 1 for the first page. */
  page: number
  hasPrevious: boolean
  hasNext: boolean
  /** A page is loading: both buttons wait, so a second click cannot act on stale cursors. */
  busy: boolean
  onPrevious: () => void
  onNext: () => void
}

/** Previous / next for a list paged by cursors, with the page number between them. */
export function CursorNav({ page, hasPrevious, hasNext, busy, onPrevious, onNext }: CursorNavProps) {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('audit.pager.label')} className="flex items-center justify-between gap-2">
      <Button type="button" variant="outline" size="sm" disabled={!hasPrevious || busy} onClick={onPrevious}>
        <ChevronLeft aria-hidden />
        {t('audit.pager.previous')}
      </Button>
      <span className="tabular text-sm text-muted-foreground" aria-live="polite">
        {t('audit.pager.page', { page })}
      </span>
      <Button type="button" variant="outline" size="sm" disabled={!hasNext || busy} onClick={onNext}>
        {t('audit.pager.next')}
        <ChevronRight aria-hidden />
      </Button>
    </nav>
  )
}
