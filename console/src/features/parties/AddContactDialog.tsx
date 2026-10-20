import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { z } from 'zod'
import { Field } from '@/components/common/Field'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { FormDialog, SelectField } from './FormDialog'
import { useAddContact } from './queries'
import {
  CONTACT_FIELD_OF,
  CONTACT_KINDS,
  contactSchema,
  showFieldProblems,
  toContactRequest,
  type ContactFormValues,
} from './sub-forms'

interface AddDialogProps {
  partyId: string
  onClose: () => void
}

/** Add an email address or a phone number to a party. */
export function AddContactDialog({ partyId, onClose }: AddDialogProps) {
  const { t } = useTranslation()
  const add = useAddContact(partyId)
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<ContactFormValues, unknown, z.output<typeof contactSchema>>({
    resolver: zodResolver(contactSchema),
    defaultValues: { kind: 'EMAIL', value: '', isPrimary: false },
  })
  const kind = useWatch({ control, name: 'kind' })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await add.mutateAsync(toContactRequest(values))
      toast.success(t('parties.contacts.added'))
      onClose()
    } catch (error) {
      if (!showFieldProblems(error, CONTACT_FIELD_OF, setError)) setFailure({ error })
    }
  })

  return (
    <FormDialog
      title={t('parties.contacts.add')}
      pending={isSubmitting}
      failure={failure}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
    >
      <Controller
        control={control}
        name="kind"
        render={({ field }) => (
          <SelectField
            label={t('parties.contacts.kindLabel')}
            value={field.value}
            options={CONTACT_KINDS}
            optionLabel={(option) => t(`parties.contacts.kind.${option}`)}
            onChange={field.onChange}
          />
        )}
      />
      <Field label={t('parties.contacts.value')} error={errors.value?.message}>
        {(aria) => (
          <Input
            {...aria}
            {...register('value')}
            type={kind === 'EMAIL' ? 'email' : 'tel'}
            inputMode={kind === 'EMAIL' ? 'email' : 'tel'}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
          />
        )}
      </Field>
      <Controller
        control={control}
        name="isPrimary"
        render={({ field }) => (
          <div className="flex items-center gap-2">
            <Checkbox
              id="contact-primary"
              checked={field.value}
              onCheckedChange={(checked) => field.onChange(checked === true)}
            />
            <Label htmlFor="contact-primary">{t('parties.contacts.primary')}</Label>
          </div>
        )}
      />
    </FormDialog>
  )
}
