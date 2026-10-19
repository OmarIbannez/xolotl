import { Info, ShieldAlert, ShieldCheck, type LucideIcon } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyValues } from '@/components/common/page'
import { useFormat, useLocale } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { formatTimestamp } from './format'
import { showInvisibles } from './safe-text'
import type { ChainVerification } from './types'
import { isKnownReason, reasonKey } from './verify-reason'

/**
 * The three things a verification can say. A chain with no events is valid but has proven
 * nothing yet, so it is not drawn like one that was walked and found intact.
 */
type VerifyOutcome = 'valid' | 'empty' | 'broken'

function outcomeOf(result: ChainVerification): VerifyOutcome {
  if (!result.valid) return 'broken'
  return result.entries_checked === 0 ? 'empty' : 'valid'
}

// Colour is never the only signal: each outcome has its own icon and its own words.
const TONE: Record<VerifyOutcome, { icon: LucideIcon; frame: string; iconColor: string }> = {
  valid: { icon: ShieldCheck, frame: 'border-good/40 bg-good/10', iconColor: 'text-good' },
  empty: { icon: Info, frame: 'border-info/40 bg-info/10', iconColor: 'text-info' },
  broken: { icon: ShieldAlert, frame: 'border-critical/50 bg-critical/10', iconColor: 'text-critical' },
}

interface VerifyCardProps {
  result: ChainVerification
  /** When the check was asked for; the verdict is only good for that moment. */
  checkedAt: Date
}

/** The verdict of `GET /v1/audit/verify`: intact (green), empty (neutral) or broken at a sequence number (red). */
export function VerifyCard({ result, checkedAt }: VerifyCardProps) {
  const { t } = useTranslation()
  const format = useFormat()
  const locale = useLocale()
  const titleId = useId()
  const outcome = outcomeOf(result)
  const { icon: Icon, frame, iconColor } = TONE[outcome]

  const copy = {
    valid: { title: t('audit.verify.valid.title'), body: t('audit.verify.valid.body') },
    empty: { title: t('audit.verify.empty.title'), body: t('audit.verify.empty.body') },
    broken: { title: t('audit.verify.broken.title'), body: t('audit.verify.broken.body') },
  }[outcome]

  const facts: [ReactNode, ReactNode][] = []
  if (outcome === 'valid') {
    facts.push([t('audit.verify.entriesChecked'), format.number(result.entries_checked)])
  }
  if (outcome === 'broken') {
    if (result.first_broken_seq != null) {
      // A sequence number names an event, so it is printed as it is (no thousands separators).
      facts.push([
        t('audit.verify.broken.firstBrokenSeq'),
        <span className="tabular font-mono">{result.first_broken_seq}</span>,
      ])
    }
    facts.push([t('audit.verify.broken.entriesChecked'), format.number(result.entries_checked)])
    facts.push([t('audit.verify.broken.reason'), t(reasonKey(result.reason))])
    if (result.reason && !isKnownReason(result.reason)) {
      // Do not hide what the API said just because this console cannot translate it.
      facts.push([
        t('audit.verify.broken.rawReason'),
        <span className="font-mono text-xs">{showInvisibles(result.reason)}</span>,
      ])
    }
  }

  return (
    <section
      aria-labelledby={titleId}
      data-outcome={outcome}
      className={cn('animate-fade-in-up flex gap-3 rounded-xl border p-4', frame)}
    >
      <Icon className={cn('mt-0.5 size-5 shrink-0', iconColor)} aria-hidden />
      <div className="min-w-0 flex-1 space-y-3">
        <div className="space-y-1">
          <h2 id={titleId} className="font-heading text-base leading-snug font-semibold">
            {copy.title}
          </h2>
          <p className="text-sm">{copy.body}</p>
        </div>
        {facts.length > 0 && <KeyValues items={facts} />}
        <p className="text-xs text-muted-foreground">
          {t('audit.verify.checkedAt', { at: formatTimestamp(checkedAt, locale) })}
        </p>
      </div>
    </section>
  )
}
