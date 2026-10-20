import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { ListKind, MatchStatus, MatchSummary } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { BandBadge } from './BandBadge'
import { DelistedSuggestionBadge, MatchStatusBadge } from './MatchStatusBadge'
import { useMatches, type MatchFilters } from './queries'

/** The queue's tabs, alerts first (plan Deviation 9: the API orders by creation only). */
const BANDS = ['ALERT', 'LOW_CONFIDENCE'] as const
const STATUSES: readonly MatchStatus[] = ['OPEN', 'ESCALATED', 'FALSE_POSITIVE', 'TRUE_MATCH', 'DELISTED', 'SUPERSEDED']
const LIST_KINDS: readonly ListKind[] = ['SANCTION', 'FISCAL', 'BLOCKED', 'PEP', 'INTERNAL']
const AGES = ['1', '7', '30'] as const
const ALL = 'ALL'
const DAY_MS = 86_400_000

type Band = (typeof BANDS)[number]
type Age = (typeof AGES)[number]

const isBand = (value: string): value is Band => (BANDS as readonly string[]).includes(value)
const isAge = (value: string): value is Age => (AGES as readonly string[]).includes(value)

/** Drop the keys left unset, so the request and the cache key carry only real filters. */
function compact(filters: MatchFilters): MatchFilters {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)) as MatchFilters
}

/**
 * The screening queue (Coincidencias): findings by band tab, filtered by status (open by
 * default), list kind, list code and age. Every row opens the finding's detail.
 */
export function MatchesPage() {
  const { t } = useTranslation()
  const [band, setBand] = useState<Band>('ALERT')
  const [status, setStatus] = useState<MatchStatus | undefined>('OPEN')
  const [listKind, setListKind] = useState<ListKind | undefined>(undefined)
  const [listCode, setListCode] = useState('')
  const [appliedCode, setAppliedCode] = useState<string | undefined>(undefined)
  const [age, setAge] = useState<{ days: Age; before: string } | undefined>(undefined)
  const [cursor, setCursor] = useState<string | undefined>(undefined)

  const filters = compact({
    band,
    status,
    list_kind: listKind,
    list_code: appliedCode,
    created_before: age?.before,
  })
  const matches = useMatches(filters, cursor)
  const page = matches.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  // Every filter change starts again from the first page.
  function narrow(change: () => void) {
    change()
    setCursor(undefined)
  }

  function chooseAge(value: string) {
    narrow(() =>
      setAge(isAge(value) ? { days: value, before: new Date(Date.now() - Number(value) * DAY_MS).toISOString() } : undefined),
    )
  }

  function applyCode(event: FormEvent) {
    event.preventDefault()
    const code = listCode.trim().toUpperCase()
    narrow(() => setAppliedCode(code === '' ? undefined : code))
  }

  const columns: Column<MatchSummary>[] = [
    {
      id: 'subject',
      header: t('screening.matches.table.subject'),
      cell: (row) => (
        <Link to={`/screening/matches/${row.id}`} className="font-medium underline-offset-4 hover:underline">
          {row.party_id === null ? t('screening.matches.table.adHoc') : t('screening.matches.table.party')}
          <span className="ml-1.5 font-mono text-xs text-muted-foreground">{row.subject_id.slice(0, 8)}</span>
        </Link>
      ),
    },
    {
      id: 'list',
      header: t('screening.matches.table.list'),
      cell: (row) => <span className="font-mono text-xs">{row.list_code}</span>,
    },
    { id: 'band', header: t('screening.matches.table.band'), cell: (row) => <BandBadge band={row.band} /> },
    {
      id: 'score',
      header: t('screening.matches.table.score'),
      cell: (row) => <span className="tabular">{row.score}</span>,
      className: 'text-right',
    },
    {
      id: 'status',
      header: t('screening.matches.table.status'),
      cell: (row) => (
        <div className="flex flex-wrap gap-1">
          <MatchStatusBadge status={row.status} />
          {row.suggested_disposition === 'DELISTED' && <DelistedSuggestionBadge />}
        </div>
      ),
    },
    { id: 'createdAt', header: t('screening.matches.table.createdAt'), cell: (row) => <DateTimeCell value={row.created_at} /> },
  ]

  return (
    <>
      <PageHeader title={t('screening.matches.title')} description={t('screening.matches.description')} />

      <Card className="animate-fade-in-up">
        <CardHeader className="space-y-4">
          <Tabs value={band} onValueChange={(value) => isBand(value) && narrow(() => setBand(value))}>
            <TabsList aria-label={t('screening.matches.tabs.label')}>
              {BANDS.map((value) => (
                <TabsTrigger key={value} value={value}>
                  {t(`screening.matches.tabs.${value}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t('screening.matches.filters.status')}>
              {(control) => (
                <Select
                  value={status ?? ALL}
                  onValueChange={(value) => narrow(() => setStatus(value === ALL ? undefined : (value as MatchStatus)))}
                >
                  <SelectTrigger {...control} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL}>{t('screening.matches.filters.statusAll')}</SelectItem>
                    {STATUSES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`screening.status.${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label={t('screening.matches.filters.listKind')}>
              {(control) => (
                <Select
                  value={listKind ?? ALL}
                  onValueChange={(value) => narrow(() => setListKind(value === ALL ? undefined : (value as ListKind)))}
                >
                  <SelectTrigger {...control} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL}>{t('screening.matches.filters.listKindAll')}</SelectItem>
                    {LIST_KINDS.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`privateLists.kind.${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <form onSubmit={applyCode} className="flex items-end gap-2">
              <div className="flex-1">
                <Field label={t('screening.matches.filters.listCode')}>
                  {(control) => (
                    <Input
                      {...control}
                      value={listCode}
                      maxLength={100}
                      placeholder={t('screening.matches.filters.listCodePlaceholder')}
                      className="font-mono text-xs"
                      onChange={(event) => setListCode(event.target.value)}
                    />
                  )}
                </Field>
              </div>
              <Button type="submit" variant="outline">
                {t('screening.matches.filters.apply')}
              </Button>
            </form>
            <Field label={t('screening.matches.filters.age')}>
              {(control) => (
                <Select value={age?.days ?? ALL} onValueChange={chooseAge}>
                  <SelectTrigger {...control} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL}>{t('screening.matches.filters.ageAny')}</SelectItem>
                    {AGES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`screening.matches.filters.age${value}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
          </div>
        </CardHeader>
        <CardContent>
          {matches.isError ? (
            <ErrorBlock error={matches.error} onRetry={() => void matches.refetch()} />
          ) : (
            <div className={cn(matches.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <DataTable
                label={t('screening.matches.table.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(row) => row.id}
                loading={matches.isPending}
                empty={t('screening.matches.empty')}
              />
            </div>
          )}
        </CardContent>
        {showPager && (
          <CardFooter>
            <div className="w-full">
              <CursorPager
                cursor={cursor}
                nextCursor={page?.next_cursor}
                onChange={setCursor}
                busy={matches.isPlaceholderData}
              />
            </div>
          </CardFooter>
        )}
      </Card>
    </>
  )
}
