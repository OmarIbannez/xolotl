import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { z } from 'zod'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { FormDialog, SelectField } from './FormDialog'
import { useAddRelationship } from './queries'
import {
  RELATIONSHIP_FIELD_OF,
  RELATIONSHIP_TYPES,
  relationshipSchema,
  showFieldProblems,
  toRelationshipRequest,
  type RelationshipFormValues,
} from './sub-forms'

interface AddDialogProps {
  partyId: string
  onClose: () => void
}

/** Record that this party relates to another one (a shareholder, a director, a representative). */
export function AddRelationshipDialog({ partyId, onClose }: AddDialogProps) {
  const { t } = useTranslation()
  const add = useAddRelationship(partyId)
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RelationshipFormValues, unknown, z.output<typeof relationshipSchema>>({
    resolver: zodResolver(relationshipSchema),
    defaultValues: { toPartyId: '', relType: 'SHAREHOLDER', ownershipPct: '', controlBasis: '', validFrom: '' },
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await add.mutateAsync(toRelationshipRequest(values))
      toast.success(t('parties.relationships.added'))
      onClose()
    } catch (error) {
      if (!showFieldProblems(error, RELATIONSHIP_FIELD_OF, setError)) setFailure({ error })
    }
  })

  return (
    <FormDialog
      title={t('parties.relationships.add')}
      pending={isSubmitting}
      failure={failure}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
    >
      <Field label={t('parties.relationships.to')} error={errors.toPartyId?.message}>
        {(aria) => (
          <Input {...aria} {...register('toPartyId')} autoComplete="off" spellCheck={false} className="font-mono" autoFocus />
        )}
      </Field>
      <Controller
        control={control}
        name="relType"
        render={({ field }) => (
          <SelectField
            label={t('parties.relationships.typeLabel')}
            value={field.value}
            options={RELATIONSHIP_TYPES}
            optionLabel={(type) => t(`parties.relationships.type.${type}`)}
            onChange={field.onChange}
          />
        )}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t('parties.relationships.ownership')} error={errors.ownershipPct?.message}>
          {(aria) => <Input {...aria} {...register('ownershipPct')} inputMode="decimal" autoComplete="off" />}
        </Field>
        <Field label={t('parties.relationships.validFrom')} error={errors.validFrom?.message}>
          {(aria) => <Input {...aria} {...register('validFrom')} type="date" autoComplete="off" />}
        </Field>
      </div>
      <Field label={t('parties.relationships.controlBasis')} error={errors.controlBasis?.message}>
        {(aria) => <Input {...aria} {...register('controlBasis')} autoComplete="off" />}
      </Field>
    </FormDialog>
  )
}
