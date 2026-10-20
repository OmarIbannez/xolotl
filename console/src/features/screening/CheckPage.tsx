import { zodResolver } from '@hookform/resolvers/zod'
import { Plus, X } from 'lucide-react'
import { useState } from 'react'
import { Controller, useFieldArray, useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { hasProblemCode } from '@/api/errors'
import type { CheckResponse, CheckStaffResponse, IdentifierType } from '@/api/types'
import { CheckboxGroup } from '@/components/common/CheckboxGroup'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { KeyValues, PageHeader } from '@/components/common/page'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { FormAlert } from '@/features/auth/FormAlert'
import { useListOverview } from '@/features/lists/queries'
import { IDENTIFIER_TYPES } from '@/features/parties/form'
import {
  checkFieldProblems,
  checkSchema,
  emptyCheck,
  ENTITY_TYPES,
  GENDERS,
  MAX_IDENTIFIERS,
  MAX_NAMES,
  toCheckRequest,
  type CheckFormValues,
  type CheckValues,
} from './check-form'
import { HitList } from './HitList'
import { isStaffCheck, useCheck } from './queries'
import { SubjectStatusBadge } from './SubjectStatusBadge'

const NO_GENDER = 'NONE'

/** The codes of the tenant's lists that have an active version, for the optional list filter. */
function useScreenableLists(): string[] {
  const overview = useListOverview()
  return (overview.data?.items ?? []).filter((list) => list.enabled && list.active_seq !== null).map((list) => list.code)
}

function CheckResult({ result }: { result: CheckResponse | CheckStaffResponse }) {
  const { t } = useTranslation()
  return (
    <Card className="animate-fade-in-up max-w-3xl" aria-live="polite">
      <CardHeader>
        <CardTitle>{t('screening.check.result')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <KeyValues
          items={[
            [t('screening.party.status'), <SubjectStatusBadge key="status" status={result.status} />],
            [t('screening.check.screenedAt'), <DateTimeCell key="at" value={result.screened_at} />],
          ]}
        />
        {isStaffCheck(result) && <HitList hits={result.hits} empty={t('screening.check.noHits')} />}
      </CardContent>
    </Card>
  )
}

/**
 * Consulta ad hoc: screen a person or an organisation that is not a party, with up to three
 * names, the person's name parts, birth date and sex, nationalities, countries, up to ten
 * identifiers and, optionally, only some of the tenant's lists. The staff result shows the
 * status and the hits. What was typed and the result live in this page only: leaving it forgets
 * both, and nothing goes to the URL, a query key or storage.
 */
export function CheckPage() {
  const { t } = useTranslation()
  const check = useCheck()
  const lists = useScreenableLists()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    control,
    reset,
    setValue,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<CheckFormValues, unknown, CheckValues>({
    resolver: zodResolver(checkSchema),
    defaultValues: emptyCheck(),
  })
  const names = useFieldArray({ control, name: 'names' })
  const identifiers = useFieldArray({ control, name: 'identifiers' })
  const entityType = useWatch({ control, name: 'entityType' })
  const person = entityType === 'PERSON'
  const result = check.data?.data

  function switchTo(next: CheckFormValues['entityType']) {
    setValue('entityType', next)
    if (next === 'ORGANIZATION') {
      // The person-only inputs are not sent for an organisation; nothing hidden may block it.
      for (const field of ['givenNames', 'firstSurname', 'secondSurname', 'birthDate', 'gender', 'nationalities'] as const) {
        setValue(field, '')
      }
    }
  }

  function clear() {
    reset(emptyCheck())
    check.reset()
    setFailure(null)
  }

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await check.mutateAsync(toCheckRequest(values))
    } catch (error) {
      const problems = checkFieldProblems(error)
      for (const { field, message } of problems) setError(field, { type: 'server', message })
      if (problems.length === 0) setFailure({ error })
    }
  })

  const identifierInvalid = { status: 422, message: t('screening.check.identifierInvalid') }

  return (
    <>
      <PageHeader title={t('screening.check.title')} description={t('screening.check.description')} />
      <div className="space-y-6">
        <Card className="animate-fade-in-up max-w-3xl">
          <form onSubmit={(event) => void submit(event)} noValidate autoComplete="off">
            <CardContent className="space-y-5">
              {failure && (
                <FormAlert
                  error={failure.error}
                  {...(hasProblemCode(failure.error, 'IDENTIFIER_INVALID') && { whenStatus: identifierInvalid })}
                />
              )}
              <fieldset className="space-y-1.5">
                <legend className="text-sm font-medium">{t('screening.check.entityType')}</legend>
                <div className="flex gap-2">
                  {ENTITY_TYPES.map((type) => (
                    <Button
                      key={type}
                      type="button"
                      variant={entityType === type ? 'default' : 'outline'}
                      aria-pressed={entityType === type}
                      disabled={isSubmitting}
                      onClick={() => switchTo(type)}
                    >
                      {t(`parties.type.${type}`)}
                    </Button>
                  ))}
                </div>
              </fieldset>

              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">{t('screening.check.names')}</legend>
                <p className="text-xs text-muted-foreground">{t('screening.check.namesHint')}</p>
                {names.fields.map((name, index) => (
                  <div key={name.id} className="flex items-end gap-2">
                    <div className="flex-1">
                      <Field label={t('screening.check.name', { n: index + 1 })} error={errors.names?.[index]?.value?.message}>
                        {(aria) => <Input {...aria} {...register(`names.${index}.value`)} autoFocus={index === 0} />}
                      </Field>
                    </div>
                    {names.fields.length > 1 && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={t('screening.check.removeName', { n: index + 1 })}
                        onClick={() => names.remove(index)}
                      >
                        <X aria-hidden />
                      </Button>
                    )}
                  </div>
                ))}
                {names.fields.length < MAX_NAMES && (
                  <Button type="button" variant="outline" size="sm" onClick={() => names.append({ value: '' })}>
                    <Plus aria-hidden />
                    {t('screening.check.addName')}
                  </Button>
                )}
              </fieldset>

              {person && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label={t('screening.check.givenNames')} error={errors.givenNames?.message}>
                    {(aria) => <Input {...aria} {...register('givenNames')} />}
                  </Field>
                  <Field label={t('screening.check.firstSurname')} error={errors.firstSurname?.message}>
                    {(aria) => <Input {...aria} {...register('firstSurname')} />}
                  </Field>
                  <Field label={t('screening.check.secondSurname')} error={errors.secondSurname?.message}>
                    {(aria) => <Input {...aria} {...register('secondSurname')} />}
                  </Field>
                  <Field label={t('screening.check.birthDate')} error={errors.birthDate?.message}>
                    {(aria) => <Input {...aria} {...register('birthDate')} type="date" />}
                  </Field>
                  <Controller
                    control={control}
                    name="gender"
                    render={({ field }) => (
                      <Field label={t('screening.check.gender.label')} error={errors.gender?.message}>
                        {(aria) => (
                          <Select
                            value={field.value === '' ? NO_GENDER : field.value}
                            onValueChange={(next) => field.onChange(next === NO_GENDER ? '' : next)}
                          >
                            <SelectTrigger {...aria} className="w-full">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value={NO_GENDER}>{t('screening.check.gender.none')}</SelectItem>
                              {GENDERS.map((gender) => (
                                <SelectItem key={gender} value={gender}>
                                  {t(`screening.check.gender.${gender}`)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        )}
                      </Field>
                    )}
                  />
                  <Field
                    label={t('screening.check.nationalities')}
                    hint={t('screening.check.countriesHint')}
                    error={errors.nationalities?.message}
                  >
                    {(aria) => <Input {...aria} {...register('nationalities')} className="font-mono uppercase" />}
                  </Field>
                </div>
              )}

              <Field label={t('screening.check.countries')} hint={t('screening.check.countriesHint')} error={errors.countries?.message}>
                {(aria) => <Input {...aria} {...register('countries')} className="font-mono uppercase" />}
              </Field>

              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">{t('screening.check.identifiers')}</legend>
                {identifiers.fields.map((identifier, index) => (
                  <div key={identifier.id} className="grid items-end gap-2 sm:grid-cols-[12rem_1fr_auto]">
                    <Controller
                      control={control}
                      name={`identifiers.${index}.idType`}
                      render={({ field }) => (
                        <Field
                          label={t('screening.check.identifierType', { n: index + 1 })}
                          error={errors.identifiers?.[index]?.idType?.message}
                        >
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
                    <Field
                      label={t('screening.check.identifierValue', { n: index + 1 })}
                      error={errors.identifiers?.[index]?.value?.message}
                    >
                      {(aria) => (
                        <Input
                          {...aria}
                          {...register(`identifiers.${index}.value`)}
                          autoCapitalize="characters"
                          spellCheck={false}
                          className="font-mono"
                        />
                      )}
                    </Field>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={t('screening.check.removeIdentifier', { n: index + 1 })}
                      onClick={() => identifiers.remove(index)}
                    >
                      <X aria-hidden />
                    </Button>
                  </div>
                ))}
                {identifiers.fields.length < MAX_IDENTIFIERS && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => identifiers.append({ idType: 'RFC', value: '' })}
                  >
                    <Plus aria-hidden />
                    {t('screening.check.addIdentifier')}
                  </Button>
                )}
              </fieldset>

              {lists.length > 0 && (
                <Controller
                  control={control}
                  name="lists"
                  render={({ field }) => (
                    <CheckboxGroup
                      legend={t('screening.check.lists')}
                      hint={t('screening.check.listsHint')}
                      options={lists.map((code) => ({ value: code, label: <span className="font-mono text-xs">{code}</span> }))}
                      value={field.value}
                      onChange={field.onChange}
                    />
                  )}
                />
              )}
            </CardContent>
            <CardFooter className="justify-end gap-2">
              <Button type="button" variant="outline" disabled={isSubmitting} onClick={clear}>
                {t('screening.check.clear')}
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? t('screening.check.submitting') : t('screening.check.submit')}
              </Button>
            </CardFooter>
          </form>
        </Card>
        {result && <CheckResult result={result} />}
      </div>
    </>
  )
}
