import { CircleAlert, CircleCheck } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { hasProblemCode } from '@/api/errors'
import type { CaptureLimitView, CapturePeriodResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Field } from '@/components/common/Field'
import { PageHeader } from '@/components/common/page'
import { EmptyBlock, ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { FormAlert } from '@/features/auth/FormAlert'
import { mexicoCityToday } from '@/features/transactions/time'
import { cn } from '@/lib/utils'
import { CapturesTable } from './CapturesTable'
import { PeriodTable } from './PeriodTable'
import { useCapturePeriods, useCaptures, useClosePeriod, useEffectiveCaptures } from './queries'

const TIER_LIMIT = 'TIER_LIMIT'

/** VERIFIED or UNVERIFIED, as the Políticas page shows a regulatory value's provenance. */
function ConfidenceBadge({ confidence, legalBasis }: { confidence: string; legalBasis: string }) {
  const { t } = useTranslation()
  const verified = confidence === 'VERIFIED'
  const Icon = verified ? CircleCheck : CircleAlert
  return (
    <Badge variant={verified ? 'good' : 'warning'} title={legalBasis}>
      <Icon aria-hidden />
      {verified ? t('policy.provenance.VERIFIED') : t('policy.provenance.UNVERIFIED')}
    </Badge>
  )
}

/**
 * Where "Proponer ajuste" leads: the Políticas page (the SP-02 override flow), told which value
 * the proposal is about. A capture threshold by its code, a tier limit by `<tier>/monthly_ops`.
 */
function proposeHref(row: CaptureLimitView): string {
  const key = row.kind === TIER_LIMIT ? 'limit' : 'capture'
  return `/policy?${new URLSearchParams({ [key]: row.code }).toString()}`
}

/** The effective policy's captures and tier limits, read only, with their provenance. */
function LimitsCard() {
  const { t } = useTranslation()
  const [on, setOn] = useState(() => mexicoCityToday())
  const [product, setProduct] = useState<string | undefined>(undefined)
  const [draft, setDraft] = useState({ on, product: '' })
  const limits = useEffectiveCaptures(on, product)

  function apply(event: FormEvent) {
    event.preventDefault()
    if (draft.on !== '') setOn(draft.on)
    const trimmed = draft.product.trim()
    setProduct(trimmed === '' ? undefined : trimmed)
  }

  const columns: Column<CaptureLimitView>[] = [
    { id: 'code', header: t('captures.limits.table.code'), cell: (row) => <span className="font-mono text-xs">{row.code}</span> },
    { id: 'kind', header: t('captures.limits.table.kind'), cell: (row) => t(`captures.kind.${row.kind}`) },
    {
      id: 'threshold',
      header: t('captures.limits.table.threshold'),
      // The decimal exactly as the pack writes it, then its unit: never parsed into a float.
      cell: (row) => <span className="tabular whitespace-nowrap">{`${row.threshold} ${row.unit}`}</span>,
    },
    { id: 'rule', header: t('captures.limits.table.rule'), cell: (row) => t(`captures.limits.rule.${row.conversion_rule}`) },
    {
      id: 'window',
      header: t('captures.limits.table.window'),
      cell: (row) => (row.window === null ? '—' : <span className="font-mono text-xs">{row.window}</span>),
    },
    {
      id: 'period',
      header: t('captures.limits.table.period'),
      cell: (row) =>
        row.period === 'MONTH' || row.period === 'QUARTER' ? t(`captures.limits.periodOf.${row.period}`) : (row.period ?? '—'),
    },
    {
      id: 'report',
      header: t('captures.limits.table.report'),
      cell: (row) => (row.report_code === null ? '—' : <span className="font-mono text-xs">{row.report_code}</span>),
    },
    {
      id: 'basis',
      header: t('captures.limits.table.basis'),
      cell: (row) => <span className="block max-w-xs text-xs whitespace-normal">{row.legal_basis}</span>,
    },
    {
      id: 'confidence',
      header: t('captures.limits.table.confidence'),
      cell: (row) => <ConfidenceBadge confidence={row.confidence} legalBasis={row.legal_basis} />,
    },
    {
      id: 'propose',
      header: <span className="sr-only">{t('captures.limits.table.propose')}</span>,
      cell: (row) => (
        <Button asChild size="sm" variant="ghost">
          <Link to={proposeHref(row)} aria-label={`${t('captures.propose')} ${row.code}`}>
            {t('captures.propose')}
          </Link>
        </Button>
      ),
    },
  ]

  let body
  if (hasProblemCode(limits.error, 'POLICY_NOT_ASSIGNED')) body = <EmptyBlock>{t('captures.limits.noPolicy')}</EmptyBlock>
  else if (limits.isError) body = <ErrorBlock error={limits.error} onRetry={() => void limits.refetch()} />
  else {
    const source = limits.data?.[0]?.source
    body = (
      <div className="space-y-2">
        <DataTable
          label={t('captures.limits.label')}
          columns={columns}
          rows={limits.data}
          getRowId={(row) => row.code}
          loading={limits.isPending}
          empty={t('captures.limits.empty')}
        />
        {source !== undefined && (
          <p className="text-xs text-muted-foreground">{t('captures.limits.source', { source })}</p>
        )}
      </div>
    )
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('captures.limits.title')}</CardTitle>
        <CardDescription>{t('captures.limits.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={apply} className="flex flex-wrap items-end gap-3">
          <div className="w-44">
            <Field label={t('captures.limits.on')}>
              {(control) => (
                <Input {...control} type="date" value={draft.on} onChange={(event) => setDraft({ ...draft, on: event.target.value })} />
              )}
            </Field>
          </div>
          <div className="w-56">
            <Field label={t('captures.limits.product')} hint={t('captures.limits.productHint')}>
              {(control) => (
                <Input {...control} value={draft.product} onChange={(event) => setDraft({ ...draft, product: event.target.value })} />
              )}
            </Field>
          </div>
          <Button type="submit" variant="outline">
            {t('captures.limits.apply')}
          </Button>
        </form>
        {body}
      </CardContent>
    </Card>
  )
}

interface PeriodsCardProps {
  onShowEvents: (period: CapturePeriodResponse) => void
}

/** The capture periods with their reconciliation; a closer closes an ended one after a warning. */
function PeriodsCard({ onShowEvents }: PeriodsCardProps) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [closing, setClosing] = useState<CapturePeriodResponse | undefined>(undefined)
  const periods = useCapturePeriods({}, cursor)
  const close = useClosePeriod()
  const page = periods.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  function cancel() {
    close.reset()
    setClosing(undefined)
  }

  function confirm(period: CapturePeriodResponse) {
    close.mutate(period.id, {
      onSuccess: () => {
        toast.success(t('captures.close.done'))
        setClosing(undefined)
      },
    })
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('captures.period.title')}</CardTitle>
        <CardDescription>{t('captures.period.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {periods.isError ? (
          <ErrorBlock error={periods.error} onRetry={() => void periods.refetch()} />
        ) : (
          <div className={cn(periods.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <PeriodTable
              rows={page?.items}
              loading={periods.isPending}
              today={mexicoCityToday()}
              canClose={hasRole(ACCESS.capturesClose)}
              onClose={setClosing}
              onShowEvents={onShowEvents}
            />
          </div>
        )}
        {showPager && (
          <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={periods.isPlaceholderData} />
        )}
      </CardContent>
      {closing !== undefined && (
        <ConfirmDialog
          title={t('captures.close.title', { capture: closing.capture_code, period: closing.period_key })}
          description={t('captures.close.confirm')}
          confirmLabel={t('captures.close.action')}
          pendingLabel={t('captures.close.pending')}
          pending={close.isPending}
          alert={close.isError ? <FormAlert error={close.error} /> : undefined}
          onConfirm={() => confirm(closing)}
          onCancel={cancel}
        />
      )}
    </Card>
  )
}

/** Which period the captures list is narrowed to; undefined for every capture. */
interface Chosen {
  capture_code: string
  period_key: string
}

/** The reportable events, newest first, of every period or of the one chosen in the table. */
function EventsCard({ chosen, onShowAll }: { chosen: Chosen | undefined; onShowAll: () => void }) {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const captures = useCaptures(chosen ?? {}, cursor)
  const page = captures.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)
  const title = useRef<HTMLDivElement>(null)

  // The card is mounted again for each chosen period: the focus follows the choice to the list it
  // narrowed, so a keyboard or screen-reader user is not left on a button whose effect is elsewhere.
  useEffect(() => {
    if (chosen !== undefined) title.current?.focus()
  }, [chosen])

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle ref={title} tabIndex={-1} className="outline-none">
          {t('captures.events.title')}
        </CardTitle>
        <CardDescription>
          {chosen === undefined
            ? t('captures.events.description')
            : t('captures.events.filtered', { capture: chosen.capture_code, period: chosen.period_key })}
        </CardDescription>
        {chosen !== undefined && (
          <CardAction>
            <Button type="button" size="sm" variant="outline" onClick={onShowAll}>
              {t('captures.events.all')}
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {captures.isError ? (
          <ErrorBlock error={captures.error} onRetry={() => void captures.refetch()} />
        ) : (
          <div className={cn(captures.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <CapturesTable
              label={t('captures.events.label')}
              rows={page?.items}
              loading={captures.isPending}
              empty={t('captures.events.empty')}
            />
          </div>
        )}
        {showPager && (
          <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={captures.isPlaceholderData} />
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Reglas › Capturas y límites: the effective policy's captures and tier limits (read only; a
 * change is proposed as a policy override), the capture periods with their reconciliation and
 * the manual close, and the captured operations of a chosen period.
 */
export function CapturesPage() {
  const { t } = useTranslation()
  const [chosen, setChosen] = useState<Chosen | undefined>(undefined)
  // The list starts from its first page whenever the chosen period changes.
  const eventsKey = chosen === undefined ? 'all' : `${chosen.capture_code}|${chosen.period_key}`

  return (
    <>
      <PageHeader title={t('captures.title')} description={t('captures.description')} />
      <div className="space-y-6">
        <LimitsCard />
        <PeriodsCard
          onShowEvents={(period) => setChosen({ capture_code: period.capture_code, period_key: period.period_key })}
        />
        <EventsCard key={eventsKey} chosen={chosen} onShowAll={() => setChosen(undefined)} />
      </div>
    </>
  )
}
