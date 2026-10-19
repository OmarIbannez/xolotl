import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { z } from 'zod'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { FormDialog, SelectField } from './FormDialog'
import { useAddAddress } from './queries'
import {
  ADDRESS_FIELD_OF,
  ADDRESS_TYPES,
  addressSchema,
  emptyAddress,
  showFieldProblems,
  toAddressRequest,
  type AddressFormValues,
} from './sub-forms'

interface AddDialogProps {
  partyId: string
  onClose: () => void
}

type TextField = Exclude<keyof AddressFormValues, 'addressType'>

/** Add an address to a party. Its lines are personal data: the API stores them encrypted. */
export function AddAddressDialog({ partyId, onClose }: AddDialogProps) {
  const { t } = useTranslation()
  const add = useAddAddress(partyId)
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<AddressFormValues, unknown, z.output<typeof addressSchema>>({
    resolver: zodResolver(addressSchema),
    defaultValues: emptyAddress(),
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await add.mutateAsync(toAddressRequest(values))
      toast.success(t('parties.addresses.added'))
      onClose()
    } catch (error) {
      if (!showFieldProblems(error, ADDRESS_FIELD_OF, setError)) setFailure({ error })
    }
  })

  const text = (name: TextField, label: string, extra: { type?: string; className?: string } = {}) => (
    <Field label={label} error={errors[name]?.message}>
      {(aria) => <Input {...aria} {...register(name)} autoComplete="off" {...extra} />}
    </Field>
  )

  return (
    <FormDialog
      title={t('parties.addresses.add')}
      pending={isSubmitting}
      failure={failure}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
    >
      <Controller
        control={control}
        name="addressType"
        render={({ field }) => (
          <SelectField
            label={t('parties.addresses.typeLabel')}
            value={field.value}
            options={ADDRESS_TYPES}
            optionLabel={(type) => t(`parties.addresses.type.${type}`)}
            onChange={field.onChange}
          />
        )}
      />
      {text('street', t('parties.addresses.street'))}
      <div className="grid gap-4 sm:grid-cols-2">
        {text('extNumber', t('parties.addresses.extNumber'))}
        {text('intNumber', t('parties.addresses.intNumber'))}
        {text('neighbourhood', t('parties.addresses.neighbourhood'))}
        {text('city', t('parties.addresses.city'))}
        {text('postalCode', t('parties.addresses.postalCode'), { className: 'font-mono' })}
        {text('countryCode', t('parties.addresses.country'), { className: 'font-mono uppercase' })}
        {text('adminAreaCode', t('parties.addresses.adminArea'), { className: 'font-mono' })}
        {text('municipalityCode', t('parties.addresses.municipality'), { className: 'font-mono' })}
        {text('validFrom', t('parties.addresses.validFrom'), { type: 'date' })}
      </div>
    </FormDialog>
  )
}
