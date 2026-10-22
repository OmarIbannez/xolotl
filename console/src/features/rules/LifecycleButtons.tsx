import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { isApiError } from '@/api/errors'
import type { RuleAction, RuleBlockedReason, VersionResponse } from '@/api/types'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { FormAlert } from '@/features/auth/FormAlert'
import { mexicoCityToday } from '@/features/transactions/time'
import { useTransition, type Transition } from './queries'

type LifecycleAction = Extract<RuleAction, 'SUBMIT' | 'APPROVE' | 'REJECT' | 'ACTIVATE' | 'RETIRE'>

/** The lifecycle's steps in the order a version takes them (spec section 9.4). */
const ACTIONS: readonly LifecycleAction[] = ['SUBMIT', 'APPROVE', 'REJECT', 'ACTIVATE', 'RETIRE']
/** Exhaustive over the schema, so a regenerated schema with a new reason fails the typecheck here. */
const REASON_SET: Record<RuleBlockedReason, true> = {
  STATE: true,
  ROLE_REQUIRED: true,
  NOT_AUTHOR: true,
  SAME_AUTHOR: true,
  SIMULATION_MISSING: true,
  SIMULATION_STALE: true,
  PARAM_OUT_OF_BOUNDS: true,
}
const REASONS = Object.keys(REASON_SET) as RuleBlockedReason[]
const REASON_MAX = 500
const isReason = (value: string): value is RuleBlockedReason => (REASONS as readonly string[]).includes(value)

/**
 * An action the user may not take, worth showing: one the version's state allows but something
 * else refuses (the author approving, a stale simulation). An action refused for the state
 * belongs to another stage of the lifecycle and is left out.
 */
function worthShowing(reasons: readonly RuleBlockedReason[]): boolean {
  return reasons.length > 0 && !reasons.includes('STATE')
}

/** A disabled button whose reasons a tooltip and a screen-reader description give. */
function BlockedAction({ action, reasons }: { action: LifecycleAction; reasons: readonly RuleBlockedReason[] }) {
  const { t } = useTranslation()
  const id = useId()
  const text = reasons.map((reason) => t(`rules.blocked.${reason}`)).join(' ')
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A disabled button takes no pointer or focus, so the wrapper is what shows the tooltip. */}
        <span tabIndex={0} className="inline-flex rounded-lg">
          <Button type="button" variant="outline" disabled aria-describedby={id}>
            {t(`rules.action.${action}`)}
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

/** The refusal of the last attempt: the problem, and the reasons a 409 carried. */
function Refusal({ error }: { error: unknown }) {
  const { t } = useTranslation()
  const reasons = isApiError(error) ? error.blockedReasons.filter(isReason) : []
  return (
    <div className="space-y-2">
      <FormAlert error={error} />
      {reasons.length > 0 && (
        <ul className="list-disc pl-5 text-sm">
          {reasons.map((reason) => (
            <li key={reason}>{t(`rules.blocked.${reason}`)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface FormDialogProps {
  title: string
  description: string
  submitLabel: string
  pending: boolean
  error: unknown
  onSubmit: () => void
  onCancel: () => void
  children: ReactNode
}

/** A dialog with a small form (the reject reason, the activation date). */
function FormDialog({ title, description, submitLabel, pending, error, onSubmit, onCancel, children }: FormDialogProps) {
  const { t } = useTranslation()
  function submit(event: FormEvent) {
    event.preventDefault()
    onSubmit()
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onCancel()
      }}
    >
      <DialogContent showCloseButton={!pending} className="sm:max-w-md">
        <form onSubmit={submit} noValidate className="space-y-4">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {children}
          {error !== null && <Refusal error={error} />}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? t('rules.action.running') : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The lifecycle steps of a version: a button for each action in `allowed_actions`, and the
 * actions this stage allows but the user may not take disabled, with `blocked_reasons` as their
 * tooltip ("El aprobador debe ser distinto del autor"). The API decides; nothing is re-derived
 * here. Reject asks for a reason, activate for a date and an optional product, retire for a
 * confirmation.
 */
export function LifecycleButtons({ ruleId, version }: { ruleId: string; version: VersionResponse }) {
  const { t } = useTranslation()
  const transition = useTransition(ruleId, version.version)
  const [open, setOpen] = useState<'REJECT' | 'ACTIVATE' | 'RETIRE' | undefined>(undefined)
  const [reason, setReason] = useState('')
  const [reasonProblem, setReasonProblem] = useState<string | undefined>(undefined)
  const today = mexicoCityToday()
  const [effectiveFrom, setEffectiveFrom] = useState(today)
  const [product, setProduct] = useState('')
  const allowed = new Set<RuleAction>(version.allowed_actions)
  const shown = ACTIONS.filter((action) => allowed.has(action) || worthShowing(version.blocked_reasons[action] ?? []))

  function send(step: Transition) {
    transition.mutate(step, {
      onSuccess: () => {
        toast.success(t(`rules.action.done.${step.action}`))
        setOpen(undefined)
        setReason('')
        transition.reset()
      },
    })
  }

  function start(action: LifecycleAction) {
    transition.reset()
    if (action === 'SUBMIT' || action === 'APPROVE') send({ action })
    else setOpen(action)
  }

  function reject() {
    const trimmed = reason.trim()
    if (trimmed.length === 0) {
      setReasonProblem(t('rules.reject.reasonRequired'))
      return
    }
    setReasonProblem(undefined)
    send({ action: 'REJECT', body: { reason: trimmed } })
  }

  function cancel() {
    setOpen(undefined)
    setReasonProblem(undefined)
    transition.reset()
  }

  const dialogError = transition.isError ? transition.error : null

  return (
    <div className="space-y-3">
      <TooltipProvider>
        <div role="group" aria-label={t('rules.action.label')} className="flex flex-wrap gap-2">
          {shown.map((action) =>
            allowed.has(action) ? (
              <Button
                key={action}
                type="button"
                variant={action === 'REJECT' || action === 'RETIRE' ? 'outline' : 'default'}
                disabled={transition.isPending}
                onClick={() => start(action)}
              >
                {t(`rules.action.${action}`)}
              </Button>
            ) : (
              <BlockedAction key={action} action={action} reasons={version.blocked_reasons[action] ?? []} />
            ),
          )}
        </div>
      </TooltipProvider>
      {shown.length === 0 && <p className="text-sm text-muted-foreground">{t('rules.action.none')}</p>}
      {open === undefined && transition.isError && <Refusal error={transition.error} />}

      {open === 'REJECT' && (
        <FormDialog
          title={t('rules.reject.title')}
          description={t('rules.reject.description')}
          submitLabel={t('rules.action.REJECT')}
          pending={transition.isPending}
          error={dialogError}
          onSubmit={reject}
          onCancel={cancel}
        >
          <Field label={t('rules.reject.reason')} hint={t('rules.reject.notStored')} error={reasonProblem}>
            {(control) => (
              <Textarea {...control} value={reason} maxLength={REASON_MAX} onChange={(event) => setReason(event.target.value)} />
            )}
          </Field>
        </FormDialog>
      )}
      {open === 'ACTIVATE' && (
        <FormDialog
          title={t('rules.activate.title')}
          description={t('rules.activate.description')}
          submitLabel={t('rules.action.ACTIVATE')}
          pending={transition.isPending}
          error={dialogError}
          onSubmit={() =>
            send({
              action: 'ACTIVATE',
              body: { effective_from: effectiveFrom, product: product.trim() === '' ? null : product.trim() },
            })
          }
          onCancel={cancel}
        >
          <Field label={t('rules.activate.effectiveFrom')} hint={t('rules.activate.effectiveFromHint')}>
            {(control) => (
              <Input
                {...control}
                type="date"
                min={today}
                required
                value={effectiveFrom}
                onChange={(event) => setEffectiveFrom(event.target.value)}
              />
            )}
          </Field>
          <Field label={t('rules.activate.product')} hint={t('rules.activate.productHint')}>
            {(control) => (
              <Input
                {...control}
                value={product}
                maxLength={64}
                className="font-mono text-xs"
                onChange={(event) => setProduct(event.target.value)}
              />
            )}
          </Field>
        </FormDialog>
      )}
      {open === 'RETIRE' && (
        <ConfirmDialog
          title={t('rules.retire.title')}
          description={t('rules.retire.description')}
          confirmLabel={t('rules.action.RETIRE')}
          pendingLabel={t('rules.action.running')}
          pending={transition.isPending}
          alert={dialogError === null ? undefined : <Refusal error={dialogError} />}
          onConfirm={() => send({ action: 'RETIRE' })}
          onCancel={cancel}
        />
      )}
    </div>
  )
}
