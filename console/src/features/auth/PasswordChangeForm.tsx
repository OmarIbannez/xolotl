import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { z } from 'zod'
import { useAuth } from '@/auth/use-auth'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { msgKey } from '@/i18n/validation'
import { FormAlert } from './FormAlert'
import { MIN_PASSWORD_LENGTH, passwordLength } from './password-strength'
import { PasswordStrengthHint } from './PasswordStrengthHint'

const schema = z
  .object({
    currentPassword: z.string().min(1, msgKey('required')),
    newPassword: z
      .string()
      .refine((value) => passwordLength(value) >= MIN_PASSWORD_LENGTH, msgKey('passwordMin')),
    confirmPassword: z.string().min(1, msgKey('required')),
  })
  .superRefine((values, ctx) => {
    if (values.newPassword !== values.confirmPassword) {
      ctx.addIssue({ code: 'custom', path: ['confirmPassword'], message: msgKey('passwordMismatch') })
    }
    if (values.newPassword === values.currentPassword) {
      ctx.addIssue({ code: 'custom', path: ['newPassword'], message: msgKey('passwordSame') })
    }
  })
type Values = z.infer<typeof schema>

interface PasswordChangeFormProps {
  /** Put the cursor in the first field on mount: right for a page that is only this form. */
  autoFocus?: boolean
  /** The API accepted the change (a forced-change gate, if there was one, is already lifted). */
  onChanged?: () => void
}

/**
 * Current password, new password (at least 12 characters, different from the current one)
 * and its confirmation. Shared by the forced change after a first sign-in and by the
 * Security page. A 422 is shown inside the form; success clears the fields and says so.
 */
export function PasswordChangeForm({ autoFocus = false, onChanged }: PasswordChangeFormProps) {
  const { t } = useTranslation()
  const { changePassword } = useAuth()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { currentPassword: '', newPassword: '', confirmPassword: '' },
  })
  const newPassword = useWatch({ control, name: 'newPassword' })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await changePassword({
        currentPassword: values.currentPassword,
        newPassword: values.newPassword,
      })
      // Passwords do not stay in the form once they have been used.
      reset()
      toast.success(t('auth.setupPassword.success'))
      onChanged?.()
    } catch (error) {
      setFailure({ error })
    }
  })

  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="space-y-4">
      {failure && (
        // The API answers 422 both for a wrong current password and for a rejected new one.
        <FormAlert
          error={failure.error}
          whenStatus={{ status: 422, message: t('auth.setupPassword.rejected') }}
        />
      )}
      <Field label={t('auth.setupPassword.current')} error={errors.currentPassword?.message}>
        {(field) => (
          <Input
            {...field}
            {...register('currentPassword')}
            type="password"
            autoComplete="current-password"
            autoFocus={autoFocus}
          />
        )}
      </Field>
      <Field
        label={t('auth.setupPassword.new')}
        hint={t('auth.setupPassword.rules')}
        error={errors.newPassword?.message}
      >
        {(field) => (
          <Input {...field} {...register('newPassword')} type="password" autoComplete="new-password" />
        )}
      </Field>
      <PasswordStrengthHint password={newPassword} />
      <Field label={t('auth.setupPassword.confirm')} error={errors.confirmPassword?.message}>
        {(field) => (
          <Input {...field} {...register('confirmPassword')} type="password" autoComplete="new-password" />
        )}
      </Field>
      <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? t('auth.setupPassword.submitting') : t('auth.setupPassword.submit')}
      </Button>
    </form>
  )
}
