import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import type { DeadlineRule, HolidayScope, HolidaysResponse } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Field } from '@/components/common/Field'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import {
  DEADLINE_DEFAULTS,
  DEADLINE_KINDS,
  deadlineSchema,
  fieldsOf,
  ROLLS,
  SCOPES,
  toAnchor,
  toDeadlineRule,
  type DeadlineField,
  type DeadlineFormValues,
  type DeadlineValues,
} from './deadline-form'
import { useDeadline, useHolidays } from './queries'

type Holiday = HolidaysResponse['items'][number]

/** The years the holiday file covers (`policy-packs/_holidays/MX.yaml`). */
const YEARS = [2025, 2026, 2027] as const

/** This year, or the nearest one the holiday file covers. */
function defaultYear(): number {
  const year = new Date().getFullYear()
  return Math.min(Math.max(year, YEARS[0]), YEARS[YEARS.length - 1] ?? year)
}

/** The current minute where the viewer is, as a datetime-local input holds it. */
function localNow(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
}

function HolidaysTable({ scope, year }: { scope: HolidayScope; year: number }) {
  const { t } = useTranslation()
  const holidays = useHolidays(scope, year)
  const columns: Column<Holiday>[] = [
    { id: 'day', header: t('calendar.holidays.day'), cell: (row) => row.day, className: 'tabular' },
    { id: 'name', header: t('calendar.holidays.name'), cell: (row) => row.name },
    {
      id: 'source',
      header: t('calendar.holidays.source'),
      cell: (row) => <span className="text-muted-foreground">{row.source}</span>,
    },
  ]
  if (holidays.isError) return <ErrorBlock error={holidays.error} onRetry={() => void holidays.refetch()} />
  return (
    <div className={cn(holidays.isPlaceholderData && 'opacity-60 transition-opacity')}>
      <DataTable
        label={`${t('calendar.holidays.label')} ${scope}`}
        columns={columns}
        rows={holidays.data?.items}
        getRowId={(row) => row.day}
        loading={holidays.isPending}
        empty={t('calendar.holidays.empty')}
      />
    </div>
  )
}

function HolidaysCard() {
  const { t } = useTranslation()
  const [scope, setScope] = useState<HolidayScope>('NATIONAL')
  const [year, setYear] = useState(defaultYear)
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('calendar.holidays.label')}</CardTitle>
      </CardHeader>
      <CardContent>
        <Tabs value={scope} onValueChange={(value) => setScope(value as HolidayScope)}>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <TabsList aria-label={t('calendar.scope')}>
              {SCOPES.map((value) => (
                <TabsTrigger key={value} value={value} className="font-mono text-xs">
                  {value}
                </TabsTrigger>
              ))}
            </TabsList>
            <div className="w-28">
              <Field label={t('calendar.year')}>
                {(control) => (
                  <Select value={String(year)} onValueChange={(value) => setYear(Number(value))}>
                    <SelectTrigger {...control} className="w-full tabular">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {YEARS.map((value) => (
                        <SelectItem key={value} value={String(value)}>
                          {value}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </Field>
            </div>
          </div>
          {SCOPES.map((value) => (
            <TabsContent key={value} value={value} className="pt-2">
              <HolidaysTable scope={value} year={year} />
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  )
}

/** The text inputs of the evaluator, by field; `roll` is a select of its own. */
const TEXT_INPUTS: Record<Exclude<DeadlineField, 'roll'>, { type: string; placeholder?: string }> = {
  n: { type: 'number' },
  monthOffset: { type: 'number' },
  day: { type: 'number' },
  hours: { type: 'number' },
  yearOffset: { type: 'number' },
  start: { type: 'text', placeholder: 'MM-DD' },
  end: { type: 'text', placeholder: 'MM-DD' },
  at: { type: 'datetime-local' },
  time: { type: 'text', placeholder: 'HH:MM' },
}

interface Asked {
  rule: DeadlineRule
  anchor: string
  scope: HolidayScope
}

/** Any rule: the query is disabled until the form has been submitted. */
const NO_RULE: DeadlineRule = { kind: 'FIXED', params: {} }

function DeadlineEvaluator() {
  const { t } = useTranslation()
  const format = useFormat()
  const [asked, setAsked] = useState<Asked | null>(null)
  const deadline = useDeadline(asked?.rule ?? NO_RULE, asked?.anchor ?? '', asked?.scope ?? 'NATIONAL', asked !== null)
  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<DeadlineFormValues, unknown, DeadlineValues>({
    resolver: zodResolver(deadlineSchema),
    defaultValues: { ...DEADLINE_DEFAULTS, anchor: localNow() },
  })
  const kind = useWatch({ control, name: 'kind' })

  const submit = handleSubmit((values) => {
    setAsked({ rule: toDeadlineRule(values), anchor: toAnchor(values.anchor), scope: values.scope })
  })

  let result = null
  if (asked !== null) {
    if (deadline.isError) result = <ErrorBlock error={deadline.error} onRetry={() => void deadline.refetch()} />
    else if (deadline.data === undefined) result = <LoadingBlock rows={2} />
    else {
      const answer = deadline.data
      result = (
        <div className="space-y-3">
          <KeyValues
            items={[
              [
                t('calendar.evaluator.result'),
                <span key="due" className="text-base font-medium tabular" title={answer.due_at}>
                  {format.dateTime(answer.due_at)}
                </span>,
              ],
            ]}
          />
          <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-xs">
            {JSON.stringify(answer.rule, null, 2)}
          </pre>
        </div>
      )
    }
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('calendar.evaluator.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={(event) => void submit(event)} noValidate className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Controller
              control={control}
              name="kind"
              render={({ field }) => (
                <Field label={t('calendar.evaluator.kind')}>
                  {(aria) => (
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger {...aria} className="w-full font-mono text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {DEADLINE_KINDS.map((value) => (
                          <SelectItem key={value} value={value} className="font-mono text-xs">
                            {value}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </Field>
              )}
            />
            <Field label={t('calendar.evaluator.anchor')} error={errors.anchor?.message}>
              {(aria) => <Input {...aria} {...register('anchor')} type="datetime-local" />}
            </Field>
            <Controller
              control={control}
              name="scope"
              render={({ field }) => (
                <Field label={t('calendar.evaluator.scope')}>
                  {(aria) => (
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger {...aria} className="w-full font-mono text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {SCOPES.map((value) => (
                          <SelectItem key={value} value={value} className="font-mono text-xs">
                            {value}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </Field>
              )}
            />
            {fieldsOf(kind).map((name) =>
              name === 'roll' ? (
                <Controller
                  key={name}
                  control={control}
                  name="roll"
                  render={({ field, fieldState }) => (
                    <Field label={t('calendar.evaluator.roll')} error={fieldState.error?.message}>
                      {(aria) => (
                        <Select value={field.value} onValueChange={field.onChange}>
                          <SelectTrigger {...aria} className="w-full font-mono text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ROLLS.map((value) => (
                              <SelectItem key={value} value={value} className="font-mono text-xs">
                                {value}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </Field>
                  )}
                />
              ) : (
                <Field key={name} label={t(`calendar.evaluator.${name}`)} error={errors[name]?.message}>
                  {(aria) => (
                    <Input
                      {...aria}
                      {...register(name)}
                      type={TEXT_INPUTS[name].type}
                      placeholder={TEXT_INPUTS[name].placeholder}
                      autoComplete="off"
                      className="tabular"
                    />
                  )}
                </Field>
              ),
            )}
          </div>
          <Button type="submit" disabled={deadline.isFetching}>
            {t('calendar.evaluator.submit')}
          </Button>
        </form>
        {result}
      </CardContent>
    </Card>
  )
}

/** The regulatory calendar: non-business days per scope and year, and the deadline evaluator. */
export function CalendarPage() {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title={t('calendar.title')} description={t('calendar.description')} />
      <div className="space-y-6">
        <HolidaysCard />
        <DeadlineEvaluator />
      </div>
    </>
  )
}
