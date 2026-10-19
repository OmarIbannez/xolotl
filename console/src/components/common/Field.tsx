import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Label } from '@/components/ui/label'
import { isValidationKey } from '@/i18n/validation'

interface FieldControlProps {
  id: string
  'aria-invalid': boolean
  'aria-describedby': string | undefined
}

interface FieldProps {
  label: string
  hint?: string | undefined
  /** A `msgKey(...)` id from the form schema, or any text. */
  error?: string | undefined
  /** Receives the props that tie the control to its label, hint and error. */
  children: (control: FieldControlProps) => ReactNode
}

/** A label, a control, an optional hint and an error message, wired for screen readers. */
export function Field({ label, hint, error, children }: FieldProps) {
  const { t } = useTranslation()
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children({ id, 'aria-invalid': error !== undefined, 'aria-describedby': describedBy })}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {isValidationKey(error) ? t(`validation.${error}`) : error}
        </p>
      )}
    </div>
  )
}
