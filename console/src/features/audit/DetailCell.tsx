import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Sheet, SheetTrigger } from '@/components/ui/sheet'
import { previewDetail } from './detail'
import { DetailSheet } from './DetailSheet'
import type { AuditEvent } from './types'

/**
 * An event's detail as text in a `<pre>`: one line of JSON, cut short when long, with a
 * button that opens the whole thing in a side panel. It is only ever a text node, never
 * markup, whatever the detail contains.
 */
export function DetailCell({ event }: { event: AuditEvent }) {
  const { t } = useTranslation()
  const preview = useMemo(() => previewDetail(event.detail), [event.detail])

  if (preview === null) {
    return <span className="whitespace-nowrap text-muted-foreground">{t('audit.detail.none')}</span>
  }

  return (
    <div className="max-w-xs min-w-56 space-y-1">
      <pre className="font-mono text-xs break-all whitespace-pre-wrap">{preview.text}</pre>
      {preview.truncated && (
        <Sheet>
          <SheetTrigger asChild>
            <Button
              type="button"
              variant="link"
              size="xs"
              className="h-auto p-0"
              aria-label={t('audit.detail.expandFor', { seq: event.seq })}
            >
              {t('audit.detail.expand')}
            </Button>
          </SheetTrigger>
          <DetailSheet event={event} />
        </Sheet>
      )}
    </div>
  )
}
