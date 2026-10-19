import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { isValidationKey } from '@/i18n/validation'

export interface CheckboxOption<V extends string> {
  value: V
  label: ReactNode
}

interface CheckboxGroupProps<V extends string> {
  legend: string
  options: readonly CheckboxOption<V>[]
  /** The values currently chosen. */
  value: readonly V[]
  onChange: (value: V[]) => void
  hint?: string | undefined
  /** A `msgKey(...)` id from the form schema, or any text. */
  error?: string | undefined
  /** More controls that belong to the group, after the checkboxes. */
  children?: ReactNode
}

/**
 * A multi-select as a group of checkboxes, for a short fixed list (the five roles, the preset
 * scopes). A fieldset gives it one name for screen readers; the hint and the error are tied to
 * it, like `Field` does for a single control. Toggling an option leaves the other chosen
 * values, including any that are not among the options, as they are.
 */
export function CheckboxGroup<V extends string>({
  legend,
  options,
  value,
  onChange,
  hint,
  error,
  children,
}: CheckboxGroupProps<V>) {
  const { t } = useTranslation()
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined

  function toggle(option: V, checked: boolean) {
    const others = value.filter((chosen) => chosen !== option)
    onChange(checked ? [...others, option] : others)
  }

  return (
    <fieldset aria-describedby={describedBy} className="space-y-2">
      <legend className="text-sm leading-none font-medium">{legend}</legend>
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      <ul className="space-y-1.5">
        {options.map((option, index) => {
          const optionId = `${id}-${index}`
          return (
            <li key={option.value} className="flex items-center gap-2">
              <Checkbox
                id={optionId}
                checked={value.includes(option.value)}
                onCheckedChange={(checked) => toggle(option.value, checked === true)}
              />
              <Label htmlFor={optionId} className="font-normal">
                {option.label}
              </Label>
            </li>
          )
        })}
      </ul>
      {children}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {isValidationKey(error) ? t(`validation.${error}`) : error}
        </p>
      )}
    </fieldset>
  )
}
