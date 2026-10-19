import { Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { useEffect, useId, useReducer, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/states'
import { PageHeader } from '@/components/common/page'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { AuditTable } from './AuditTable'
import { CursorNav } from './CursorNav'
import { useAuditEvents, useVerifyAuditChain } from './queries'
import { VerifyCard } from './VerifyCard'
import { auditViewReducer, currentCursor, FIRST_PAGE, pageNumber } from './view-state'

/** How long typing pauses before the filter is applied (Enter applies it at once). */
const FILTER_DEBOUNCE_MS = 300

/** The API refuses an action longer than this. */
const MAX_ACTION_LENGTH = 120

/**
 * The tenant's audit log: newest first, filterable by exact action, paged with cursors, and
 * checkable end to end. Open to MLRO, ADMIN and AUDITOR_RO (the route guard and the API
 * both enforce it).
 */
export function AuditPage() {
  const { t } = useTranslation()
  const filterId = useId()
  const [draft, setDraft] = useState('')
  const [view, dispatch] = useReducer(auditViewReducer, FIRST_PAGE)
  const events = useAuditEvents({ action: view.action, cursor: currentCursor(view) })
  const verify = useVerifyAuditChain()

  // Debounce what is typed into the filter. A changed filter starts over from the newest
  // event (the reducer resets the cursors), so a stale cursor is never sent with it.
  useEffect(() => {
    const timer = window.setTimeout(
      () => dispatch({ type: 'filter', action: draft.trim() }),
      FILTER_DEBOUNCE_MS,
    )
    return () => window.clearTimeout(timer)
  }, [draft])

  function clearFilter() {
    setDraft('')
    dispatch({ type: 'filter', action: '' })
  }

  const items = events.data?.items ?? []
  const nextCursor = events.data?.next_cursor ?? undefined
  const hasPrevious = view.cursors.length > 1
  const hasNext = nextCursor !== undefined
  // While the next page loads, the old one is still on screen with its old cursor.
  const switching = events.isPlaceholderData

  let list
  if (events.isError) {
    list = (
      <div className="space-y-3">
        <ErrorBlock error={events.error} />
        <Button type="button" variant="outline" onClick={() => void events.refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    )
  } else if (events.data === undefined || (switching && items.length === 0)) {
    list = <LoadingBlock rows={6} />
  } else if (items.length === 0) {
    list = (
      <EmptyBlock>
        {view.action === ''
          ? t('audit.empty.none')
          : t('audit.empty.filtered', { action: view.action })}
      </EmptyBlock>
    )
  } else {
    list = (
      <div aria-busy={switching} className={cn('transition-opacity', switching && 'opacity-60')}>
        <AuditTable events={items} />
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={t('audit.title')}
        description={t('audit.description')}
        actions={
          <Button type="button" onClick={() => verify.mutate()} disabled={verify.isPending}>
            {verify.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <ShieldCheck aria-hidden />}
            {verify.isPending ? t('audit.verify.running') : t('audit.verify.button')}
          </Button>
        }
      />

      {/* Always mounted, so a verdict that appears is announced. */}
      <div aria-live="polite" className="empty:hidden">
        {verify.isError && (
          <div className="pb-4">
            <ErrorBlock error={verify.error} />
          </div>
        )}
        {verify.isSuccess && (
          <div className="pb-4">
            <VerifyCard result={verify.data} checkedAt={new Date(verify.submittedAt)} />
          </div>
        )}
      </div>

      <Card className="animate-fade-in-up">
        <CardContent className="space-y-4">
          <form
            role="search"
            onSubmit={(event) => {
              event.preventDefault()
              dispatch({ type: 'filter', action: draft.trim() })
            }}
            className="space-y-1.5"
          >
            <Label htmlFor={filterId}>{t('audit.filter.label')}</Label>
            <div className="flex flex-wrap gap-2">
              <Input
                id={filterId}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                maxLength={MAX_ACTION_LENGTH}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                aria-describedby={`${filterId}-hint`}
                className="max-w-sm min-w-48 flex-1 font-mono"
              />
              {draft !== '' && (
                <Button type="button" variant="ghost" onClick={clearFilter}>
                  {t('audit.filter.clear')}
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                disabled={events.isFetching}
                onClick={() => void events.refetch()}
              >
                <RefreshCw className={cn(events.isFetching && 'animate-spin')} aria-hidden />
                {t('audit.filter.refresh')}
              </Button>
            </div>
            <p id={`${filterId}-hint`} className="text-xs text-muted-foreground">
              {t('audit.filter.hint')}
            </p>
          </form>

          {list}

          {/* Only when there is somewhere to go: a list that fits on one page needs no pager.
              It stays when a page fails, so the way back is still there. */}
          {(hasPrevious || hasNext) && (
            <CursorNav
              page={pageNumber(view)}
              hasPrevious={hasPrevious}
              hasNext={hasNext}
              busy={switching}
              onPrevious={() => dispatch({ type: 'back' })}
              onNext={() => {
                if (nextCursor !== undefined) dispatch({ type: 'next', cursor: nextCursor })
              }}
            />
          )}
        </CardContent>
      </Card>
    </>
  )
}
