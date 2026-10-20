import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm, useWatch, type FieldErrors } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { fieldProblems } from '@/api/field-errors'
import type { IdentifierType } from '@/api/types'
import { Field } from '@/components/common/Field'
import { PageHeader } from '@/components/common/page'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { FormAlert } from '@/features/auth/FormAlert'
import {
  emptyParty,
  FORM_FIELD_OF,
  IDENTIFIER_TYPES,
  partySchema,
  toPartyRequest,
  type OrganizationFormValues,
  type PartyFormValues,
  type PartyValues,
  type PersonFormValues,
} from './form'
import { useCreateParty } from './queries'
import { identifierRefusal, withLeafFields } from './sub-forms'

type PartyType = PartyValues['partyType']
const PARTY_TYPES: readonly PartyType[] = ['PERSON', 'ORGANIZATION']
const SERVER_FIELDS = Object.keys(FORM_FIELD_OF) as (keyof typeof FORM_FIELD_OF)[]

/** Every field of either kind, so one error lookup serves both forms. */
type AnyPartyFields = Omit<PersonFormValues, 'partyType'> & Omit<OrganizationFormValues, 'partyType'>

/**
 * The form that registers a person or an organisation with one identifier. Like the other create
 * forms, a retry after a failure with no definite answer resends the same `Idempotency-Key`, so the
 * party is never created twice.
 */
export function CreatePartyPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useCreateParty()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    reset,
    getValues,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<PartyFormValues, unknown, PartyValues>({
    resolver: zodResolver(partySchema),
    defaultValues: emptyParty('PERSON'),
  })
  const partyType = useWatch({ control, name: 'partyType' })
  const fieldErrors = errors as FieldErrors<AnyPartyFields>

  function switchTo(next: PartyType) {
    if (next === partyType) return
    // The country and the identifier carry over; the names belong to one kind only.
    const { countryCode, identifierType, identifierValue } = getValues()
    reset({ ...emptyParty(next), countryCode, identifierType, identifierValue })
    setFailure(null)
  }

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      const { data } = await create.mutateAsync(toPartyRequest(values))
      toast.success(t('parties.create.done'))
      void navigate(`/parties/${data.id}`)
    } catch (error) {
      const problems = fieldProblems(withLeafFields(error), SERVER_FIELDS)
      for (const { field, message } of problems) {
        setError(FORM_FIELD_OF[field], { type: 'server', message })
      }
      if (problems.length === 0) setFailure({ error })
    }
  })

  return (
    <>
      <PageHeader title={t('parties.create.title')} />
      <Card className="animate-fade-in-up max-w-2xl">
        <form onSubmit={(event) => void submit(event)} noValidate>
          <CardContent className="space-y-4">
            {failure && <FormAlert error={failure.error} whenStatus={identifierRefusal(failure.error, t)} />}
            <fieldset className="space-y-1.5">
              <legend className="text-sm font-medium">{t('parties.create.partyType')}</legend>
              <div className="flex gap-2">
                {PARTY_TYPES.map((type) => (
                  <Button
                    key={type}
                    type="button"
                    variant={partyType === type ? 'default' : 'outline'}
                    aria-pressed={partyType === type}
                    disabled={isSubmitting}
                    onClick={() => switchTo(type)}
                  >
                    {t(`parties.type.${type}`)}
                  </Button>
                ))}
              </div>
            </fieldset>

            {partyType === 'PERSON' ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={t('parties.create.givenNames')} error={fieldErrors.givenNames?.message}>
                  {(aria) => <Input {...aria} {...register('givenNames')} autoComplete="off" autoFocus />}
                </Field>
                <Field label={t('parties.create.firstSurname')} error={fieldErrors.firstSurname?.message}>
                  {(aria) => <Input {...aria} {...register('firstSurname')} autoComplete="off" />}
                </Field>
                <Field label={t('parties.create.secondSurname')} error={fieldErrors.secondSurname?.message}>
                  {(aria) => <Input {...aria} {...register('secondSurname')} autoComplete="off" />}
                </Field>
                <Field label={t('parties.create.birthDate')} error={fieldErrors.birthDate?.message}>
                  {(aria) => <Input {...aria} {...register('birthDate')} type="date" autoComplete="off" />}
                </Field>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={t('parties.create.legalName')} error={fieldErrors.legalName?.message}>
                  {(aria) => <Input {...aria} {...register('legalName')} autoComplete="off" autoFocus />}
                </Field>
                <Field label={t('parties.create.tradeName')} error={fieldErrors.tradeName?.message}>
                  {(aria) => <Input {...aria} {...register('tradeName')} autoComplete="off" />}
                </Field>
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-[8rem_1fr_1fr]">
              <Field label={t('parties.create.countryCode')} error={fieldErrors.countryCode?.message}>
                {(aria) => (
                  <Input
                    {...aria}
                    {...register('countryCode')}
                    autoComplete="off"
                    autoCapitalize="characters"
                    maxLength={2}
                    className="font-mono uppercase"
                  />
                )}
              </Field>
              <Controller
                control={control}
                name="identifierType"
                render={({ field }) => (
                  <Field label={t('parties.create.identifierType')}>
                    {(aria) => (
                      <Select value={field.value} onValueChange={(next) => field.onChange(next as IdentifierType)}>
                        <SelectTrigger {...aria} className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {IDENTIFIER_TYPES.map((type) => (
                            <SelectItem key={type} value={type}>
                              {t(`parties.identifierType.${type}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </Field>
                )}
              />
              <Field label={t('parties.create.identifierValue')} error={fieldErrors.identifierValue?.message}>
                {(aria) => (
                  <Input
                    {...aria}
                    {...register('identifierValue')}
                    autoComplete="off"
                    autoCapitalize="characters"
                    spellCheck={false}
                    className="font-mono"
                  />
                )}
              </Field>
            </div>
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button type="button" variant="outline" disabled={isSubmitting} onClick={() => void navigate('/parties')}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? t('parties.create.submitting') : t('parties.create.submit')}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </>
  )
}
