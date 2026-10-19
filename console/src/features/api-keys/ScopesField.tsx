import { X } from 'lucide-react'
import { useId, useState } from 'react'
import { useController, type Control } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { CheckboxGroup } from '@/components/common/CheckboxGroup'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { isValidationKey, msgKey } from '@/i18n/validation'
import { isValidScope, PRESET_SCOPES, type ApiKeyFormValues } from './form'

const PRESET_VALUES: readonly string[] = PRESET_SCOPES.map((preset) => preset.scope)

/**
 * The scopes of a new API key: the common ones as checkboxes, plus free entry for any other
 * scope that fits the grammar. A typed scope joins the selection with "Add" (or Enter) and
 * leaves it with its chip's remove button.
 */
export function ScopesField({ control }: { control: Control<ApiKeyFormValues> }) {
  const { t } = useTranslation()
  const scopes = useController({ control, name: 'scopes' })
  const draft = useController({ control, name: 'scopeDraft' })
  // Set when "Add" was pressed on text that is not a scope; cleared by typing.
  const [rejected, setRejected] = useState(false)
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`

  const selected = scopes.field.value
  const custom = selected.filter((scope) => !PRESET_VALUES.includes(scope))
  const draftError = rejected ? msgKey('scopeFormat') : draft.fieldState.error?.message

  function add() {
    const scope = draft.field.value.trim()
    if (scope === '') return
    if (!isValidScope(scope)) {
      setRejected(true)
      return
    }
    if (!selected.includes(scope)) scopes.field.onChange([...selected, scope])
    draft.field.onChange('')
    setRejected(false)
  }

  return (
    <CheckboxGroup
      legend={t('apiKeys.create.scopes')}
      options={PRESET_SCOPES.map(({ scope, labelKey }) => ({
        value: scope,
        label: (
          <>
            <span className="font-mono text-xs">{scope}</span>
            <span className="text-muted-foreground">{t(labelKey)}</span>
          </>
        ),
      }))}
      value={selected}
      onChange={scopes.field.onChange}
      error={scopes.fieldState.error?.message}
    >
      <div className="space-y-1.5 pt-1">
        <Label htmlFor={id}>{t('apiKeys.scopes.custom')}</Label>
        <div className="flex gap-2">
          <Input
            id={id}
            value={draft.field.value}
            onChange={(event) => {
              setRejected(false)
              draft.field.onChange(event.target.value)
            }}
            onBlur={draft.field.onBlur}
            onKeyDown={(event) => {
              if (event.key !== 'Enter') return
              event.preventDefault() // Enter adds the scope; it must not submit the form
              add()
            }}
            placeholder={t('apiKeys.scopes.placeholder')}
            className="font-mono"
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-invalid={draftError !== undefined}
            aria-describedby={draftError ? `${hintId} ${errorId}` : hintId}
          />
          <Button type="button" variant="outline" onClick={add}>
            {t('apiKeys.scopes.add')}
          </Button>
        </div>
        <p id={hintId} className="text-xs text-muted-foreground">
          {t('apiKeys.scopes.customHint')}
        </p>
        {draftError && (
          <p id={errorId} role="alert" className="text-xs text-destructive">
            {isValidationKey(draftError) ? t(`validation.${draftError}`) : draftError}
          </p>
        )}
        {custom.length > 0 && (
          <ul aria-label={t('apiKeys.scopes.added')} className="flex flex-wrap gap-1.5 pt-1">
            {custom.map((scope) => (
              <li key={scope}>
                <Badge variant="mono" className="h-6 gap-1 pr-1">
                  {scope}
                  <button
                    type="button"
                    aria-label={t('apiKeys.scopes.remove', { scope })}
                    onClick={() => scopes.field.onChange(selected.filter((s) => s !== scope))}
                    className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-3" aria-hidden />
                  </button>
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </CheckboxGroup>
  )
}
