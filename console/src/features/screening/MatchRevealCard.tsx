import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { DispositionView, MatchReveal } from '@/api/types'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { arrayOf, identifier, nameOf } from '@/features/lists/entry-json'
import { calendarDay } from '@/features/parties/dates'
import { useFormat } from '@/i18n/use-locale'
import { useRevealMatch } from './queries'

function Revealed({ reveal, dispositions }: { reveal: MatchReveal; dispositions: readonly DispositionView[] }) {
  const { t } = useTranslation()
  const format = useFormat()
  const rows: [string, ReactNode][] = []
  if (reveal.subject_names.length > 0) {
    rows.push([
      t('screening.reveal.names'),
      <ul key="names" className="space-y-0.5">
        {reveal.subject_names.map((name, index) => (
          <li key={index}>{name}</li>
        ))}
      </ul>,
    ])
  }
  if (reveal.birth_date !== null) {
    rows.push([
      t('screening.reveal.birthDate'),
      <time key="birth" dateTime={reveal.birth_date}>
        {format.date(calendarDay(reveal.birth_date))}
      </time>,
    ])
  }
  if (reveal.identifiers.length > 0) {
    rows.push([
      t('screening.reveal.identifiers'),
      <ul key="identifiers" className="space-y-0.5 font-mono text-xs">
        {reveal.identifiers.map((item, index) => (
          <li key={index}>{identifier(t, item) ?? item.value}</li>
        ))}
      </ul>,
    ])
  }
  if (reveal.private_entry !== null) {
    const entry = reveal.private_entry
    const ids = arrayOf(entry.identifiers).flatMap((value) => identifier(t, value) ?? [])
    rows.push([
      t('screening.reveal.privateEntry'),
      <div key="private" className="space-y-0.5">
        <p>{nameOf(entry.primary_name) ?? '—'}</p>
        {ids.map((id, index) => (
          <p key={index} className="font-mono text-xs">
            {id}
          </p>
        ))}
      </div>,
    ])
  }
  const rationales = dispositions.flatMap((d) => {
    const text = reveal.rationales[d.id]
    return text === undefined ? [] : [{ disposition: d, text }]
  })
  if (rationales.length > 0) {
    rows.push([
      t('screening.reveal.rationales'),
      <ul key="rationales" className="space-y-2">
        {rationales.map(({ disposition, text }) => (
          <li key={disposition.id}>
            <span className="text-xs text-muted-foreground">
              {t(`screening.decision.${disposition.decision}`)} · {format.dateTime(disposition.decided_at)}
            </span>
            <p className="whitespace-pre-wrap">{text}</p>
          </li>
        ))}
      </ul>,
    ])
  }
  return (
    <div className="space-y-3">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldCheck className="size-4" aria-hidden />
        {t('screening.reveal.notice')}
      </p>
      {rows.length === 0 ? <p className="text-sm text-muted-foreground">{t('screening.reveal.empty')}</p> : <KeyValues items={rows} />}
    </div>
  )
}

/**
 * The finding's personal values (the subject's names, birth date and identifiers, a private
 * entry, the rationales), behind an explicit "Revelar": each reveal is a request the API audits
 * as `VIEW_PII`. The plaintext lives only in the mutation's result and "Ocultar" drops it.
 */
export function MatchRevealCard({ matchId, dispositions }: { matchId: string; dispositions: readonly DispositionView[] }) {
  const { t } = useTranslation()
  const reveal = useRevealMatch(matchId)

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.reveal.title')}</CardTitle>
        <CardAction>
          {reveal.isSuccess ? (
            <Button type="button" variant="outline" size="sm" onClick={() => reveal.reset()}>
              <EyeOff aria-hidden />
              {t('screening.reveal.hide')}
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate()}>
              <Eye aria-hidden />
              {reveal.isPending ? t('screening.reveal.running') : t('screening.reveal.button')}
            </Button>
          )}
        </CardAction>
      </CardHeader>
      <CardContent>
        <div aria-live="polite">{reveal.isSuccess && <Revealed reveal={reveal.data} dispositions={dispositions} />}</div>
        {reveal.isError && <ErrorBlock error={reveal.error} onRetry={() => reveal.mutate()} />}
      </CardContent>
    </Card>
  )
}
