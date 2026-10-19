import type { FormEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { FormAlert } from '@/features/auth/FormAlert'

interface FormDialogProps {
  title: string
  description?: string
  /** True while the request is out: the dialog cannot be dismissed, so its outcome is never lost. */
  pending: boolean
  /** The last failure that no field could show, with the message for its status when it has one. */
  failure: { error: unknown; whenStatus?: { status: number; message: string } | undefined } | null
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onClose: () => void
  /** The submit button's label, and its label while the request is out. */
  submitLabel?: string
  pendingLabel?: string
  children: ReactNode
}

/**
 * The frame of the party detail's add and edit forms, as `CreateStaffDialog`: open for as long as
 * it is rendered, a form-level alert above the fields, cancel and submit below.
 */
export function FormDialog({
  title,
  description,
  pending,
  failure,
  onSubmit,
  onClose,
  submitLabel,
  pendingLabel,
  children,
}: FormDialogProps) {
  const { t } = useTranslation()
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent showCloseButton={!pending} className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description !== undefined && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          {failure && <FormAlert error={failure.error} whenStatus={failure.whenStatus} />}
          {children}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? (pendingLabel ?? t('parties.saving')) : (submitLabel ?? t('parties.save'))}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface SelectFieldProps<V extends string> {
  label: string
  value: V
  options: readonly V[]
  /** The text of each option. */
  optionLabel: (value: V) => string
  onChange: (value: V) => void
  error?: string | undefined
  disabled?: boolean
}

/** A labelled `Select` over a fixed vocabulary. */
export function SelectField<V extends string>({
  label,
  value,
  options,
  optionLabel,
  onChange,
  error,
  disabled,
}: SelectFieldProps<V>) {
  return (
    <Field label={label} error={error}>
      {(aria) => (
        <Select value={value} onValueChange={(next) => onChange(next as V)} disabled={disabled}>
          <SelectTrigger {...aria} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option} value={option}>
                {optionLabel(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </Field>
  )
}
