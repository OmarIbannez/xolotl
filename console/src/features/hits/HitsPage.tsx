import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { HitOutcome, Severity } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { Field } from '@/components/common/Field'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { HitsTable } from './HitsTable'
import { SEVERITIES, useHits, useSeveritiesWithHits } from './queries'

const OUTCOMES: readonly HitOutcome[] = ['ALERT', 'BLOCK', 'REVIEW']
const AGES = ['1', '7', '30'] as const
const ALL = 'ALL'
const DAY_MS = 86_400_000

type Age = (typeof AGES)[number]

const isSeverity = (value: string): value is Severity => (SEVERITIES as readonly string[]).includes(value)
const isAge = (value: string): value is Age => (AGES as readonly string[]).includes(value)
const isOutcome = (value: string): value is HitOutcome => (OUTCOMES as readonly string[]).includes(value)

/** A code as the API stores it: trimmed and upper case; blank means no filter. */
function code(value: string): string | undefined {
  const trimmed = value.trim().toUpperCase()
  return trimmed === '' ? undefined : trimmed
}

/**
 * Hallazgos de reglas: the hits by severity tab (it opens on the most severe tab that has a hit),
 * filtered by rule, typology, outcome and age. Read only: a hit has no disposition in SP-04 (spec
 * section 17). Every row opens the hit's detail.
 */
export function HitsPage() {
  const { t } = useTranslation()
  const [chosen, setChosen] = useState<Severity | undefined>(undefined)
  const [rule, setRule] = useState('')
  const [typology, setTypology] = useState('')
  const [codes, setCodes] = useState<{ rule_code?: string; typology?: string }>({})
  const [outcome, setOutcome] = useState<HitOutcome | undefined>(undefined)
  const [age, setAge] = useState<{ days: Age; before: string } | undefined>(undefined)
  const [cursor, setCursor] = useState<string | undefined>(undefined)

  const narrowing = { ...codes, outcome, created_before: age?.before }
  const withHits = useSeveritiesWithHits(narrowing)
  // Until the user picks a tab, the queue opens on the most severe one with a hit.
  const severity = chosen ?? (withHits === undefined ? undefined : (withHits[0] ?? 'CRITICAL'))
  const hits = useHits({ ...narrowing, severity }, cursor, { enabled: severity !== undefined })
  const page = hits.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  // Every filter change starts again from the first page.
  function narrow(change: () => void) {
    change()
    setCursor(undefined)
  }

  function chooseAge(value: string) {
    narrow(() => setAge(isAge(value) ? { days: value, before: new Date(Date.now() - Number(value) * DAY_MS).toISOString() } : undefined))
  }

  function applyCodes(event: FormEvent) {
    event.preventDefault()
    narrow(() => setCodes({ rule_code: code(rule), typology: code(typology) }))
  }

  function clearCodes() {
    setRule('')
    setTypology('')
    narrow(() => setCodes({}))
  }

  return (
    <>
      <PageHeader title={t('hits.title')} description={t('hits.description')} />

      <Card className="animate-fade-in-up">
        <CardHeader className="space-y-4">
          <Tabs value={severity ?? ''} onValueChange={(value) => isSeverity(value) && narrow(() => setChosen(value))}>
            <TabsList aria-label={t('hits.tabs.label')}>
              {SEVERITIES.map((value) => (
                <TabsTrigger key={value} value={value}>
                  {t(`hits.severity.${value}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <form onSubmit={applyCodes} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" noValidate>
            <Field label={t('hits.filters.rule')}>
              {(control) => (
                <Input
                  {...control}
                  value={rule}
                  maxLength={100}
                  placeholder={t('hits.filters.rulePlaceholder')}
                  className="font-mono text-xs"
                  onChange={(event) => setRule(event.target.value)}
                />
              )}
            </Field>
            <Field label={t('hits.filters.typology')}>
              {(control) => (
                <Input
                  {...control}
                  value={typology}
                  maxLength={100}
                  placeholder={t('hits.filters.typologyPlaceholder')}
                  className="font-mono text-xs"
                  onChange={(event) => setTypology(event.target.value)}
                />
              )}
            </Field>
            <Field label={t('hits.filters.outcome')}>
              {(control) => (
                <Select
                  value={outcome ?? ALL}
                  onValueChange={(value) => narrow(() => setOutcome(isOutcome(value) ? value : undefined))}
                >
                  <SelectTrigger {...control} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL}>{t('hits.filters.outcomeAll')}</SelectItem>
                    {OUTCOMES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`hits.outcome.${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label={t('hits.filters.age')}>
              {(control) => (
                <Select value={age?.days ?? ALL} onValueChange={chooseAge}>
                  <SelectTrigger {...control} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL}>{t('hits.filters.ageAny')}</SelectItem>
                    {AGES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`hits.filters.age${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <div className="flex flex-wrap gap-2 sm:col-span-2 lg:col-span-4">
              <Button type="submit" variant="outline">
                {t('hits.filters.apply')}
              </Button>
              <Button type="button" variant="ghost" onClick={clearCodes}>
                {t('hits.filters.clear')}
              </Button>
            </div>
          </form>
        </CardHeader>
        <CardContent>
          {hits.isError ? (
            <ErrorBlock error={hits.error} onRetry={() => void hits.refetch()} />
          ) : (
            <div className={cn(hits.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <HitsTable
                label={t('hits.table.label')}
                rows={page?.items}
                loading={hits.isPending}
                empty={t('hits.empty')}
              />
            </div>
          )}
        </CardContent>
        {showPager && (
          <CardFooter>
            <div className="w-full">
              <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={hits.isPlaceholderData} />
            </div>
          </CardFooter>
        )}
      </Card>
    </>
  )
}
