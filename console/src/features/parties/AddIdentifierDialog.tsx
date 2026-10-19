import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { z } from 'zod'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { IDENTIFIER_TYPES } from './form'
import { FormDialog, SelectField } from './FormDialog'
import { useAddIdentifier } from './queries'
import {
  IDENTIFIER_FIELD_OF,
  identifierRefusal,
  identifierSchema,
  showFieldProblems,
  toIdentifierRequest,
  type IdentifierFormValues,
} from './sub-forms'

interface AddDialogProps {
  partyId: string
  onClose: () => void
}

/** Add one more identifier to a party; a duplicate or an invalid one is refused as on create. */
export function AddIdentifierDialog({ partyId, onClose }: AddDialogProps) {
  const { t } = useTranslation()
  const add = useAddIdentifier(partyId)
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<IdentifierFormValues, unknown, z.output<typeof identifierSchema>>({
    resolver: zodResolver(identifierSchema),
    defaultValues: { idType: 'RFC', value: '', countryCode: 'MX' },
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await add.mutateAsync(toIdentifierRequest(values))
      toast.success(t('parties.identifiers.added'))
      onClose()
    } catch (error) {
      if (!showFieldProblems(error, IDENTIFIER_FIELD_OF, setError)) setFailure({ error })
    }
  })

  return (
    <FormDialog
      title={t('parties.identifiers.add')}
      pending={isSubmitting}
      failure={failure && { error: failure.error, whenStatus: identifierRefusal(failure.error, t) }}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
    >
      <Controller
        control={control}
        name="idType"
        render={({ field }) => (
          <SelectField
            label={t('parties.identifiers.type')}
            value={field.value}
            options={IDENTIFIER_TYPES}
            optionLabel={(type) => t(`parties.identifierType.${type}`)}
            onChange={field.onChange}
          />
        )}
      />
      <Field label={t('parties.identifiers.value')} error={errors.value?.message}>
        {(aria) => (
          <Input
            {...aria}
            {...register('value')}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            className="font-mono"
            autoFocus
          />
        )}
      </Field>
      <Field label={t('parties.identifiers.country')} error={errors.countryCode?.message}>
        {(aria) => (
          <Input {...aria} {...register('countryCode')} autoComplete="off" maxLength={2} className="w-24 font-mono uppercase" />
        )}
      </Field>
    </FormDialog>
  )
}
