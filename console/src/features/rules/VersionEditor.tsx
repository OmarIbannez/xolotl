import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { isApiError } from '@/api/errors'
import type { KeyKind, ParamValue, ParamView, Severity, VersionResponse } from '@/api/types'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { FormAlert } from '@/features/auth/FormAlert'
import { SEVERITIES } from '@/features/hits/queries'
import {
  inputOf,
  SEGMENT_FIELDS,
  segmentInputOf,
  segmentOf,
  valueOf,
  type ParamInput,
  type SegmentField,
} from './params'
import { useUpdateVersion } from './queries'

const KEY_KINDS: readonly KeyKind[] = ['PARTY', 'RELATIONSHIP', 'ACCOUNT', 'COUNTERPARTY', 'DEVICE']
const isSeverity = (value: string): value is Severity => (SEVERITIES as readonly string[]).includes(value)
const isKeyKind = (value: string): value is KeyKind => (KEY_KINDS as readonly string[]).includes(value)

/** The bounds a parameter may move within, and where they come from, as one hint. */
function useBoundHint() {
  const { t } = useTranslation()
  return (param: ParamView) =>
    [
      param.min === null ? null : t('rules.bound.min', { value: param.min }),
      param.max === null ? null : t('rules.bound.max', { value: param.max }),
      param.choices === null ? null : t('rules.bound.choices', { values: param.choices.join(', ') }),
      param.tighten === null ? null : t(`rules.bound.tighten.${param.tighten}`),
      t('rules.bound.source', { source: param.source }),
    ]
      .filter((part) => part !== null)
      .join(' · ')
}

/**
 * The parameters the API refused, by name. A bound (`RULE_PARAM_OUT_OF_BOUNDS`, located at
 * `['params', name]`) or a value of the wrong type (`VALIDATION_FAILED` at `['body', 'params',
 * name]`). The server's English text is never shown: the field repeats the bounds instead.
 */
function refusedParams(error: unknown, params: readonly ParamView[]): Set<string> {
  const refused = new Set<string>()
  if (!isApiError(error) || error.status !== 422) return refused
  for (const { loc } of error.errors) {
    const at = loc.indexOf('params')
    const name = at < 0 ? undefined : loc[at + 1]
    if (typeof name === 'string' && params.some((param) => param.name === name)) refused.add(name)
  }
  return refused
}

interface ParamControlProps {
  param: ParamView
  value: ParamInput
  onChange: (value: ParamInput) => void
  control: { id: string; 'aria-invalid': boolean; 'aria-describedby': string | undefined }
}

/** One native control per parameter type: a select for choices, a switch, a number or a decimal. */
function ParamControl({ param, value, onChange, control }: ParamControlProps) {
  if (param.type === 'BOOL') {
    return <Switch {...control} checked={value === true} onCheckedChange={(checked) => onChange(checked)} />
  }
  const text = typeof value === 'string' ? value : ''
  if (param.choices !== null) {
    return (
      <Select value={text} onValueChange={onChange}>
        <SelectTrigger {...control} className="w-full font-mono text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {param.choices.map((choice) => (
            <SelectItem key={choice} value={choice} className="font-mono text-xs">
              {choice}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }
  if (param.type === 'INT') {
    return (
      <Input
        {...control}
        type="number"
        step={1}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        value={text}
        className="tabular"
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }
  return (
    <Input
      {...control}
      inputMode={param.type === 'DECIMAL' ? 'decimal' : undefined}
      value={text}
      className={param.type === 'DECIMAL' ? 'tabular' : 'font-mono text-xs'}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

interface VersionEditorProps {
  ruleId: string
  version: VersionResponse
  /** Told whether the form holds changes not saved yet (the page holds the simulation until then). */
  onDirtyChange?: (dirty: boolean) => void
}

/** The form's values as the version holds them. */
function formOf(version: VersionResponse) {
  return {
    params: Object.fromEntries(version.params.map((param) => [param.name, inputOf(param)])) as Record<string, ParamInput>,
    segment: segmentInputOf(version.segment),
    severity: version.severity,
    dedupKey: version.dedup.key ?? 'RELATIONSHIP',
    dedupWindow: version.dedup.window ?? '',
    cooldown: version.dedup.cooldown ?? '',
  }
}

type Form = ReturnType<typeof formOf>

/**
 * What a form would send, spelled as the API stores it: the values `valueOf`, `segmentOf` and the
 * trimmed durations make of the text (a parameter that is not a value keeps its raw text). Two
 * forms that send the same thing are the same, so "5,000" over 5000 or "n1" over "N1" is no edit.
 */
function sentOf(form: Form, version: VersionResponse) {
  return {
    params: Object.fromEntries(
      version.params.map((param) => {
        const input = form.params[param.name] ?? inputOf(param)
        return [param.name, valueOf(param, input) ?? { raw: input }]
      }),
    ),
    segment: segmentOf(form.segment),
    severity: form.severity,
    dedup: { key: form.dedupKey, window: form.dedupWindow.trim(), cooldown: form.cooldown.trim() },
  }
}

/**
 * Tune a version: its parameters within the template's bounds, the segment it applies to, its
 * severity and deduplication. The rule's text is not edited (spec section 9.4): its sentence is
 * the server's rendering, read again after each save. Only a version whose `allowed_actions`
 * has `EDIT` can be changed; any change sends it back to `DRAFT`, to be simulated again.
 */
export function VersionEditor({ ruleId, version, onDirtyChange }: VersionEditorProps) {
  const { t } = useTranslation()
  const boundHint = useBoundHint()
  const update = useUpdateVersion(ruleId, version.version)
  const editable = version.allowed_actions.includes('EDIT')
  const [initial] = useState(() => formOf(version))
  const [params, setParams] = useState<Record<string, ParamInput>>(initial.params)
  const [segment, setSegment] = useState(initial.segment)
  const [severity, setSeverity] = useState<Severity>(initial.severity)
  const [dedupKey, setDedupKey] = useState<KeyKind>(initial.dedupKey)
  const [dedupWindow, setDedupWindow] = useState(initial.dedupWindow)
  const [cooldown, setCooldown] = useState(initial.cooldown)
  const [invalid, setInvalid] = useState<ReadonlySet<string>>(new Set())
  const dirty =
    JSON.stringify(sentOf({ params, segment, severity, dedupKey, dedupWindow, cooldown }, version)) !==
    JSON.stringify(sentOf(formOf(version), version))

  /** Show what the server now holds (a save it found unchanged does not remount the form). */
  function reset(saved: VersionResponse) {
    const form = formOf(saved)
    setParams(form.params)
    setSegment(form.segment)
    setSeverity(form.severity)
    setDedupKey(form.dedupKey)
    setDedupWindow(form.dedupWindow)
    setCooldown(form.cooldown)
  }

  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])
  // A form that goes away holds nothing unsaved.
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const refused = refusedParams(update.error, version.params)
  const blocked = version.blocked_reasons.EDIT ?? []

  function submit(event: FormEvent) {
    event.preventDefault()
    const values: Record<string, ParamValue> = {}
    const wrong = new Set<string>()
    for (const param of version.params) {
      const value = valueOf(param, params[param.name] ?? inputOf(param))
      if (value === null) wrong.add(param.name)
      else values[param.name] = value
    }
    setInvalid(wrong)
    if (wrong.size > 0) return
    update.mutate(
      {
        params: values,
        segment: segmentOf(segment),
        severity,
        dedup: { key: dedupKey, window: dedupWindow.trim(), cooldown: cooldown.trim() },
      },
      {
        onSuccess: (saved) => {
          reset(saved)
          toast.success(t('rules.editor.saved'))
        },
      },
    )
  }

  function paramError(param: ParamView): string | undefined {
    if (invalid.has(param.name)) return t(`rules.editor.invalid.${param.type}`)
    if (refused.has(param.name)) return t('rules.editor.outOfBounds', { bounds: boundHint(param) })
    return undefined
  }

  const formProblem = update.isError && refused.size === 0

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('rules.editor.title', { version: version.version })}</CardTitle>
        <CardDescription>{editable ? t('rules.editor.description') : t('rules.editor.readOnly')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">{t('rules.editor.sentence')}</p>
          <p className="text-base">{version.sentence}</p>
        </div>
        {!editable && blocked.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-muted-foreground">
            {blocked.map((reason) => (
              <li key={reason}>{t(`rules.blocked.${reason}`)}</li>
            ))}
          </ul>
        )}
        <form onSubmit={submit} noValidate>
          <fieldset disabled={!editable || update.isPending} className="space-y-5">
            <fieldset className="space-y-3">
              <legend className="mb-2 text-sm font-medium">{t('rules.editor.params')}</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                {version.params.map((param) => (
                  <Field
                    key={param.name}
                    label={param.unit === null ? param.name : `${param.name} (${param.unit})`}
                    hint={param.type === 'DECIMAL' ? `${t('rules.editor.decimalHint')} ${boundHint(param)}` : boundHint(param)}
                    error={paramError(param)}
                  >
                    {(control) => (
                      <ParamControl
                        param={param}
                        value={params[param.name] ?? inputOf(param)}
                        onChange={(value) => setParams((current) => ({ ...current, [param.name]: value }))}
                        control={control}
                      />
                    )}
                  </Field>
                ))}
              </div>
            </fieldset>

            <fieldset className="space-y-3">
              <legend className="mb-2 text-sm font-medium">{t('rules.editor.segment')}</legend>
              <p className="text-xs text-muted-foreground">{t('rules.editor.segmentHint')}</p>
              <div className="grid gap-4 sm:grid-cols-3">
                {SEGMENT_FIELDS.map((field: SegmentField) => (
                  <Field key={field} label={t(`rules.editor.segmentField.${field}`)}>
                    {(control) => (
                      <Input
                        {...control}
                        value={segment[field]}
                        maxLength={200}
                        placeholder={t(`rules.editor.segmentPlaceholder.${field}`)}
                        className="font-mono text-xs"
                        onChange={(event) => setSegment((current) => ({ ...current, [field]: event.target.value }))}
                      />
                    )}
                  </Field>
                ))}
              </div>
            </fieldset>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label={t('rules.editor.severity')}>
                {(control) => (
                  <Select value={severity} onValueChange={(value) => isSeverity(value) && setSeverity(value)}>
                    <SelectTrigger {...control} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SEVERITIES.map((value) => (
                        <SelectItem key={value} value={value}>
                          {t(`hits.severity.${value}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </Field>
              <Field label={t('rules.editor.dedupKey')}>
                {(control) => (
                  <Select value={dedupKey} onValueChange={(value) => isKeyKind(value) && setDedupKey(value)}>
                    <SelectTrigger {...control} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {KEY_KINDS.map((value) => (
                        <SelectItem key={value} value={value}>
                          {t(`rules.keyKind.${value}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </Field>
              <Field label={t('rules.editor.dedupWindow')} hint={t('rules.editor.durationHint')}>
                {(control) => (
                  <Input
                    {...control}
                    value={dedupWindow}
                    maxLength={20}
                    className="font-mono text-xs"
                    onChange={(event) => setDedupWindow(event.target.value)}
                  />
                )}
              </Field>
              <Field label={t('rules.editor.cooldown')} hint={t('rules.editor.durationHint')}>
                {(control) => (
                  <Input
                    {...control}
                    value={cooldown}
                    maxLength={20}
                    className="font-mono text-xs"
                    onChange={(event) => setCooldown(event.target.value)}
                  />
                )}
              </Field>
            </div>

            {formProblem && <FormAlert error={update.error} />}
            {editable && (
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit">{update.isPending ? t('rules.editor.saving') : t('rules.editor.save')}</Button>
                <div className="space-y-0.5 text-xs text-muted-foreground">
                  <p>{t('rules.editor.resets')}</p>
                  <p>{t('rules.editor.excludesApproval')}</p>
                </div>
              </div>
            )}
          </fieldset>
        </form>
      </CardContent>
    </Card>
  )
}
