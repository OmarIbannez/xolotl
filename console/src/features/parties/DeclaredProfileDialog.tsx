import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { CustomerRelationshipResponse } from '@/api/types'
import { CheckboxGroup } from '@/components/common/CheckboxGroup'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { FormDialog } from './FormDialog'
import {
  declaredFormOf,
  declaredProfileSchema,
  INSTRUMENTS,
  toDeclaredProfileRequest,
  type DeclaredProfileFormValues,
} from './profile-form'
import { useSetDeclaredProfile } from './queries'
import { showFieldProblems } from './sub-forms'

/** The API's field names (under `declared_profile`) and the inputs that show their problems. */
const FIELD_OF = {
  monthly_inflow_minor: 'inflow',
  monthly_outflow_minor: 'outflow',
  expected_countries: 'countries',
  expected_instruments: 'instruments',
} as const

interface DeclaredProfileDialogProps {
  relationship: CustomerRelationshipResponse
  onClose: () => void
}

/**
 * Set a customer relationship's declared monthly profile: inflow and outflow in MXN (sent as
 * centavos), the countries and instruments it expects. Prefilled from the stored declaration.
 */
export function DeclaredProfileDialog({ relationship, onClose }: DeclaredProfileDialogProps) {
  const { t } = useTranslation()
  const save = useSetDeclaredProfile(relationship.id)
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<DeclaredProfileFormValues>({
    resolver: zodResolver(declaredProfileSchema),
    defaultValues: declaredFormOf(relationship.declared_profile),
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await save.mutateAsync(toDeclaredProfileRequest(values))
      toast.success(t('parties.profile.dialog.saved'))
      onClose()
    } catch (error) {
      if (!showFieldProblems(error, FIELD_OF, setError)) setFailure({ error })
    }
  })

  return (
    <FormDialog
      title={t('parties.profile.dialog.title')}
      description={t('parties.profile.dialog.description')}
      pending={isSubmitting}
      failure={failure}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
      submitLabel={t('parties.profile.dialog.submit')}
      pendingLabel={t('parties.profile.dialog.submitting')}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t('parties.profile.dialog.inflow')} hint={t('parties.profile.dialog.amountHint')} error={errors.inflow?.message}>
          {(aria) => <Input {...aria} {...register('inflow')} inputMode="decimal" autoComplete="off" className="tabular-nums" />}
        </Field>
        <Field label={t('parties.profile.dialog.outflow')} hint={t('parties.profile.dialog.amountHint')} error={errors.outflow?.message}>
          {(aria) => <Input {...aria} {...register('outflow')} inputMode="decimal" autoComplete="off" className="tabular-nums" />}
        </Field>
      </div>
      <Field label={t('parties.profile.dialog.countries')} hint={t('parties.profile.dialog.countriesHint')} error={errors.countries?.message}>
        {(aria) => <Input {...aria} {...register('countries')} autoComplete="off" className="font-mono" />}
      </Field>
      <Controller
        control={control}
        name="instruments"
        render={({ field }) => (
          <CheckboxGroup
            legend={t('parties.profile.dialog.instruments')}
            options={INSTRUMENTS.map((code): { value: string; label: string } => ({ value: code, label: t(`transactions.instrument.${code}`) }))}
            value={field.value}
            onChange={field.onChange}
            error={errors.instruments?.message}
          />
        )}
      />
    </FormDialog>
  )
}
