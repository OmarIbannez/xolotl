import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { hasProblemCode } from '@/api/errors'
import { fieldProblems } from '@/api/field-errors'
import { useCreateStaff } from '@/api/queries'
import type { StaffCreatedResponse } from '@/api/types'
import { CheckboxGroup } from '@/components/common/CheckboxGroup'
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
import { Input } from '@/components/ui/input'
import { FormAlert } from '@/features/auth/FormAlert'
import { FORM_FIELD_OF, ROLES, staffSchema, toStaffRequest, type StaffFormValues } from './form'

interface CreateStaffDialogProps {
  onClose: () => void
  /** Called once with the new account, whose `initial_password` the caller must show and then drop. */
  onCreated: (created: StaffCreatedResponse) => void
}

/**
 * The "new user" form. Open for as long as it is rendered, so the form and the submission's
 * idempotency state start afresh each time the caller shows it. Like the API key form, it
 * resends with the same `Idempotency-Key` after a failure that left the outcome unknown, and
 * cannot be dismissed while the request is out, so the one-time password always reaches
 * `onCreated`.
 */
export function CreateStaffDialog({ onClose, onCreated }: CreateStaffDialogProps) {
  const { t } = useTranslation()
  const create = useCreateStaff()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<StaffFormValues>({
    resolver: zodResolver(staffSchema),
    defaultValues: { email: '', displayName: '', roles: [] },
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      const { data } = await create.mutateAsync(toStaffRequest(values))
      onCreated(data)
    } catch (error) {
      const problems = fieldProblems(error, ['email', 'display_name', 'roles'] as const)
      for (const { field, message } of problems) {
        setError(FORM_FIELD_OF[field], { type: 'server', message })
      }
      if (problems.length === 0) setFailure({ error })
    }
  })

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !isSubmitting) onClose()
      }}
    >
      <DialogContent showCloseButton={!isSubmitting} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('staff.create.title')}</DialogTitle>
          <DialogDescription>{t('staff.create.description')}</DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => void submit(event)} noValidate className="space-y-4">
          {failure && (
            <FormAlert
              error={failure.error}
              whenStatus={
                hasProblemCode(failure.error, 'CONFLICT')
                  ? { status: 409, message: t('staff.create.conflict') }
                  : undefined
              }
            />
          )}
          <Field label={t('staff.create.email')} error={errors.email?.message}>
            {(aria) => (
              <Input
                {...aria}
                {...register('email')}
                type="email"
                inputMode="email"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
              />
            )}
          </Field>
          <Field label={t('staff.create.displayName')} error={errors.displayName?.message}>
            {(aria) => <Input {...aria} {...register('displayName')} autoComplete="off" />}
          </Field>
          <Controller
            control={control}
            name="roles"
            render={({ field, fieldState }) => (
              <CheckboxGroup
                legend={t('staff.create.roles')}
                hint={t('staff.create.rolesHint')}
                options={ROLES.map((role) => ({ value: role, label: t(`roles.${role}`) }))}
                value={field.value}
                onChange={field.onChange}
                error={fieldState.error?.message}
              />
            )}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? t('staff.create.submitting') : t('staff.create.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
