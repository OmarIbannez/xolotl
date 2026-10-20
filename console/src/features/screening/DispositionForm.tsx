import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { isApiError } from '@/api/errors'
import type { BlockedReason, DispositionRequest, MatchDetail } from '@/api/types'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { FormAlert } from '@/features/auth/FormAlert'
import { useDisposition } from './queries'

type StaffDecision = DispositionRequest['decision']

/** Exhaustive over the schema, so a regenerated schema with a new value fails the typecheck here. */
const STAFF_DECISION_SET: Record<StaffDecision, true> = {
  FALSE_POSITIVE: true,
  POSSIBLE_TRUE_MATCH: true,
  TRUE_MATCH: true,
  DELISTED: true,
}
const BLOCKED_REASON_SET: Record<BlockedReason, true> = {
  STATE: true,
  ROLE_REQUIRED: true,
  SAME_REVIEWER: true,
  NOT_DELISTED: true,
}

/** The decisions a person can take, in the API's order (spec section 9). */
const STAFF_DECISIONS = Object.keys(STAFF_DECISION_SET) as StaffDecision[]
const BLOCKED_REASONS = Object.keys(BLOCKED_REASON_SET) as BlockedReason[]
const RATIONALE_MIN = 10
const RATIONALE_MAX = 2000

const isBlockedReason = (value: string): value is BlockedReason => (BLOCKED_REASONS as readonly string[]).includes(value)

/** One decision the user may not take: a disabled button whose reasons a tooltip and the description give. */
function BlockedDecision({ decision, reasons }: { decision: StaffDecision; reasons: readonly BlockedReason[] }) {
  const { t } = useTranslation()
  const id = useId()
  const text = reasons.map((reason) => t(`screening.blocked.${reason}`)).join(' ')
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A disabled button takes no pointer or focus, so the wrapper is what shows the tooltip. */}
        <span tabIndex={0} className="inline-flex rounded-lg">
          <Button type="button" variant="outline" disabled aria-describedby={id}>
            {t(`screening.decision.${decision}`)}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
      <span id={id} className="sr-only">
        {text}
      </span>
    </Tooltip>
  )
}

/**
 * Decide a finding: one button per decision the current user may take (`allowed_actions`); the
 * other decisions are disabled and say why (`blocked_reasons`). A rationale of 10 to 2000
 * characters is required, and a confirmed match asks once more before it is sent.
 */
export function DispositionForm({ match }: { match: MatchDetail }) {
  const { t } = useTranslation()
  const disposition = useDisposition(match.id)
  const [decision, setDecision] = useState<StaffDecision | undefined>(undefined)
  const [rationale, setRationale] = useState('')
  const [problem, setProblem] = useState<string | undefined>(undefined)
  const [confirming, setConfirming] = useState(false)
  const allowed = new Set<string>(match.allowed_actions)

  function validate(): string | undefined {
    if (decision === undefined) return t('screening.disposition.chooseDecision')
    const length = rationale.trim().length
    if (length < RATIONALE_MIN) return t('screening.disposition.rationaleShort')
    if (length > RATIONALE_MAX) return t('screening.disposition.rationaleLong')
    return undefined
  }

  function send() {
    if (decision === undefined) return
    disposition.mutate(
      { decision, rationale: rationale.trim() },
      {
        onSuccess: () => {
          toast.success(t('screening.disposition.done'))
          setConfirming(false)
          setDecision(undefined)
          setRationale('')
          disposition.reset()
        },
        onError: () => setConfirming(false),
      },
    )
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const invalid = validate()
    setProblem(invalid)
    if (invalid !== undefined) return
    if (decision === 'TRUE_MATCH') setConfirming(true)
    else send()
  }

  const refused = disposition.isError && isApiError(disposition.error) ? disposition.error.blockedReasons.filter(isBlockedReason) : []

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('screening.disposition.title')}</CardTitle>
        <CardDescription>{t('screening.disposition.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} noValidate className="space-y-4">
          <TooltipProvider>
            <div role="group" aria-label={t('screening.disposition.decisions')} className="flex flex-wrap gap-2">
              {STAFF_DECISIONS.map((value) =>
                allowed.has(value) ? (
                  <Button
                    key={value}
                    type="button"
                    variant={decision === value ? 'default' : 'outline'}
                    aria-pressed={decision === value}
                    onClick={() => setDecision(value)}
                  >
                    {t(`screening.decision.${value}`)}
                  </Button>
                ) : (
                  <BlockedDecision key={value} decision={value} reasons={match.blocked_reasons[value] ?? []} />
                ),
              )}
            </div>
          </TooltipProvider>
          {match.allowed_actions.length === 0 && (
            <p className="text-sm text-muted-foreground">{t('screening.disposition.noneAllowed')}</p>
          )}
          <Field label={t('screening.disposition.rationale')} hint={t('screening.disposition.rationaleHint')} error={problem}>
            {(control) => (
              <Textarea
                {...control}
                value={rationale}
                maxLength={RATIONALE_MAX}
                disabled={match.allowed_actions.length === 0}
                onChange={(event) => setRationale(event.target.value)}
              />
            )}
          </Field>
          {disposition.isError && !confirming && (
            <div className="space-y-2">
              <FormAlert error={disposition.error} />
              {refused.length > 0 && (
                <div className="text-sm">
                  <p className="text-muted-foreground">{t('screening.disposition.refusedReasons')}</p>
                  <ul className="list-disc pl-5">
                    {refused.map((reason) => (
                      <li key={reason}>{t(`screening.blocked.${reason}`)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          <Button type="submit" disabled={match.allowed_actions.length === 0 || disposition.isPending}>
            {disposition.isPending ? t('screening.disposition.submitting') : t('screening.disposition.submit')}
          </Button>
        </form>
      </CardContent>
      {confirming && (
        <ConfirmDialog
          title={t('screening.disposition.confirmTrueMatch')}
          description={t('screening.disposition.confirmTrueMatchDescription')}
          confirmLabel={t('screening.decision.TRUE_MATCH')}
          pendingLabel={t('screening.disposition.submitting')}
          pending={disposition.isPending}
          onConfirm={send}
          onCancel={() => setConfirming(false)}
        />
      )}
    </Card>
  )
}
