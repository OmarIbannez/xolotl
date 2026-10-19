import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CopyButton } from '@/components/common/CopyButton'
import { SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { useLocale } from '@/i18n/use-locale'
import { prettyDetail } from './detail'
import { formatTimestamp } from './format'
import { showInvisibles } from './safe-text'
import type { AuditEvent } from './types'

/**
 * The side panel with one event's whole detail, indented. Render it inside the `Sheet`
 * whose trigger opened it, so focus goes back to that trigger on close. It mounts only
 * while open, and the JSON is shown as text, never as markup.
 */
export function DetailSheet({ event }: { event: AuditEvent }) {
  const { t } = useTranslation()
  const locale = useLocale()
  const json = useMemo(() => (event.detail ? prettyDetail(event.detail) : ''), [event.detail])

  return (
    <SheetContent side="right" className="w-[40rem] max-w-[92vw]">
      <div className="space-y-1 pr-8">
        <SheetTitle>{t('audit.detail.sheetTitle', { seq: event.seq })}</SheetTitle>
        <SheetDescription>
          {t('audit.detail.sheetDescription', {
            action: showInvisibles(event.action),
            at: formatTimestamp(event.at, locale),
          })}
        </SheetDescription>
      </div>
      <div className="flex justify-end">
        <CopyButton
          value={json}
          label={t('audit.detail.copy')}
          copiedMessage={t('audit.detail.copied')}
          failedMessage={t('audit.copyFailed')}
        />
      </div>
      <pre className="min-h-0 overflow-auto rounded-lg border bg-surface-2 p-3 font-mono text-xs break-words whitespace-pre-wrap">
        {json}
      </pre>
    </SheetContent>
  )
}
