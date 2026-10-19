import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm, type FieldValues, type Path, type UseFormRegister } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { OrganizationAttributes, PartyStaffResponse, PartyUpdateRequest, PersonAttributes } from '@/api/types'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { FormDialog, SelectField } from './FormDialog'
import { useUpdateParty } from './queries'
import {
  editOrganizationSchema,
  editPersonSchema,
  ORGANIZATION_FIELD_OF,
  organizationFormOf,
  PEP_STATUSES,
  PERSON_FIELD_OF,
  personFormOf,
  RESIDENCIES,
  showFieldProblems,
  toOrganizationUpdate,
  toPersonUpdate,
  type EditOrganizationValues,
  type EditPersonValues,
} from './sub-forms'

interface EditPartyDialogProps {
  party: PartyStaffResponse
  onClose: () => void
}

/**
 * Edit a party's plain attributes. Only the fields the user changed are sent (a PATCH, which needs
 * no `Idempotency-Key`); a form changed nowhere closes without a request.
 */
export function EditPartyDialog({ party, onClose }: EditPartyDialogProps) {
  if (party.person !== null) return <EditPerson partyId={party.id} person={party.person} onClose={onClose} />
  if (party.organization !== null)
    return <EditOrganization partyId={party.id} organization={party.organization} onClose={onClose} />
  return null
}

/** The patch, the toasts and the error handling both forms share. */
function useSave(partyId: string, onClose: () => void) {
  const { t } = useTranslation()
  const update = useUpdateParty(partyId)
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  async function save(patch: PartyUpdateRequest | null, onFieldProblems: (error: unknown) => boolean) {
    setFailure(null)
    if (patch === null) {
      toast.info(t('parties.edit.noChanges'))
      onClose()
      return
    }
    try {
      await update.mutateAsync(patch)
      toast.success(t('parties.edit.done'))
      onClose()
    } catch (error) {
      if (!onFieldProblems(error)) setFailure({ error })
    }
  }
  return { save, failure }
}

function TextInput<T extends FieldValues>({
  name,
  label,
  error,
  register,
  type,
  hint,
}: {
  name: Path<T>
  label: string
  error: string | undefined
  register: UseFormRegister<T>
  type?: string
  hint?: string
}) {
  return (
    <Field label={label} error={error} hint={hint}>
      {(aria) => <Input {...aria} {...register(name)} type={type} autoComplete="off" />}
    </Field>
  )
}

function EditPerson({ partyId, person, onClose }: { partyId: string; person: PersonAttributes; onClose: () => void }) {
  const { t } = useTranslation()
  const [initial] = useState(() => personFormOf(person))
  const { save, failure } = useSave(partyId, onClose)
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<EditPersonValues>({ resolver: zodResolver(editPersonSchema), defaultValues: initial })
  const submit = handleSubmit((values) =>
    save(toPersonUpdate(initial, values), (error) => showFieldProblems(error, PERSON_FIELD_OF, setError)),
  )
  const field = (name: keyof EditPersonValues, type?: string, hint?: string) => (
    <TextInput
      name={name}
      label={t(`parties.edit.${name}`)}
      error={errors[name]?.message}
      register={register}
      type={type}
      hint={hint}
    />
  )

  return (
    <FormDialog
      title={t('parties.edit.title')}
      description={t('parties.edit.description')}
      pending={isSubmitting}
      failure={failure}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
      submitLabel={t('parties.edit.submit')}
      pendingLabel={t('parties.edit.submitting')}
    >
      <Controller
        control={control}
        name="pepStatus"
        render={({ field: select }) => (
          <SelectField
            label={t('parties.edit.pepStatus')}
            value={select.value}
            options={PEP_STATUSES}
            optionLabel={(status) => t(`parties.edit.pep.${status}`)}
            onChange={select.onChange}
          />
        )}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        {field('pepSince', 'date')}
        {field('pepUntil', 'date')}
      </div>
      <Controller
        control={control}
        name="isResident"
        render={({ field: select }) => (
          <SelectField
            label={t('parties.edit.isResident')}
            value={select.value}
            options={RESIDENCIES}
            optionLabel={(value) =>
              t(value === 'YES' ? 'parties.edit.residentYes' : value === 'NO' ? 'parties.edit.residentNo' : 'parties.edit.residentUnknown')
            }
            onChange={select.onChange}
          />
        )}
      />
      {field('nationalities', undefined, t('parties.edit.nationalitiesHint'))}
      <div className="grid gap-4 sm:grid-cols-2">
        {field('occupationCode')}
        {field('economicActivityCode')}
        {field('birthCountry')}
        {field('birthState')}
        {field('gender')}
      </div>
    </FormDialog>
  )
}

function EditOrganization({
  partyId,
  organization,
  onClose,
}: {
  partyId: string
  organization: OrganizationAttributes
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [initial] = useState(() => organizationFormOf(organization))
  const { save, failure } = useSave(partyId, onClose)
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<EditOrganizationValues>({ resolver: zodResolver(editOrganizationSchema), defaultValues: initial })
  const submit = handleSubmit((values) =>
    save(toOrganizationUpdate(initial, values), (error) => showFieldProblems(error, ORGANIZATION_FIELD_OF, setError)),
  )
  const field = (name: keyof EditOrganizationValues, type?: string) => (
    <TextInput
      name={name}
      label={t(`parties.edit.${name}`)}
      error={errors[name]?.message}
      register={register}
      type={type}
    />
  )

  return (
    <FormDialog
      title={t('parties.edit.title')}
      description={t('parties.edit.description')}
      pending={isSubmitting}
      failure={failure}
      onSubmit={(event) => void submit(event)}
      onClose={onClose}
      submitLabel={t('parties.edit.submit')}
      pendingLabel={t('parties.edit.submitting')}
    >
      {field('tradeName')}
      <div className="grid gap-4 sm:grid-cols-2">
        {field('legalForm')}
        {field('incorporationDate', 'date')}
        {field('industryCode')}
        {field('registryRef')}
        {field('countryOfIncorporation')}
      </div>
    </FormDialog>
  )
}
