import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import type { TransactionStaffView } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Field } from '@/components/common/Field'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { formatMinor } from './amount'
import {
  filtersFromSearch,
  filtersToQuery,
  INSTRUMENTS,
  isUuid,
  RAILS,
  rangeIsValid,
  STATUSES,
  type TransactionFilters,
} from './filters'
import { useTransactions } from './queries'
import { MexicoCityTimeCell } from './MexicoCityTimeCell'
import { LateBadge, TransactionStatusBadge } from './TransactionStatusBadge'

const ALL = 'ALL'

/** The query string of a set of filters, for the URL: a reload or a link keeps them. */
function searchOf(filters: TransactionFilters): URLSearchParams {
  return new URLSearchParams(Object.entries(filtersToQuery(filters)).map(([key, value]) => [key, String(value)]))
}

interface FilterBarProps {
  applied: TransactionFilters
  onApply: (filters: TransactionFilters) => void
}

/**
 * The filters, edited as a draft and applied together. A reversed date range or a party id
 * that is not one is refused here: the API would answer the first with an empty page.
 */
function FilterBar({ applied, onApply }: FilterBarProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<TransactionFilters>(applied)
  const [errors, setErrors] = useState<{ party?: string; range?: string }>({})

  function set<K extends keyof TransactionFilters>(key: K, value: TransactionFilters[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  function apply(event: FormEvent) {
    event.preventDefault()
    const query = filtersToQuery(draft)
    const next = {
      party: query.party_id === undefined || isUuid(query.party_id) ? undefined : t('transactions.filters.partyIdInvalid'),
      range: rangeIsValid(query) ? undefined : t('transactions.filters.rangeInvalid'),
    }
    setErrors(next)
    if (next.party === undefined && next.range === undefined) onApply(query)
  }

  function clear() {
    setDraft({})
    setErrors({})
    onApply({})
  }

  return (
    <form onSubmit={apply} className="space-y-3" noValidate>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label={t('transactions.filters.partyId')} error={errors.party}>
          {(control) => (
            <Input
              {...control}
              value={draft.party_id ?? ''}
              maxLength={36}
              className="font-mono text-xs"
              onChange={(event) => set('party_id', event.target.value)}
            />
          )}
        </Field>
        <Field label={t('transactions.filters.externalId')}>
          {(control) => (
            <Input
              {...control}
              value={draft.external_id ?? ''}
              maxLength={128}
              className="font-mono text-xs"
              onChange={(event) => set('external_id', event.target.value)}
            />
          )}
        </Field>
        <Field label={t('transactions.filters.from')} error={errors.range}>
          {(control) => (
            <Input {...control} type="date" value={draft.from ?? ''} onChange={(event) => set('from', event.target.value)} />
          )}
        </Field>
        <Field label={t('transactions.filters.to')}>
          {(control) => (
            <Input {...control} type="date" value={draft.to ?? ''} onChange={(event) => set('to', event.target.value)} />
          )}
        </Field>
        <Field label={t('transactions.filters.status')}>
          {(control) => (
            <Select
              value={draft.status ?? ALL}
              onValueChange={(value) => set('status', STATUSES.find((status) => status === value))}
            >
              <SelectTrigger {...control} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('transactions.filters.statusAll')}</SelectItem>
                {STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`transactions.status.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label={t('transactions.filters.instrument')}>
          {(control) => (
            <Select
              value={draft.instrument ?? ALL}
              onValueChange={(value) => set('instrument', INSTRUMENTS.find((instrument) => instrument === value))}
            >
              <SelectTrigger {...control} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('transactions.filters.instrumentAll')}</SelectItem>
                {INSTRUMENTS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`transactions.instrument.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label={t('transactions.filters.rail')}>
          {(control) => (
            <Select value={draft.rail ?? ALL} onValueChange={(value) => set('rail', RAILS.find((rail) => rail === value))}>
              <SelectTrigger {...control} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('transactions.filters.railAll')}</SelectItem>
                {RAILS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`transactions.rail.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label={t('transactions.filters.late')}>
          {(control) => (
            <div className="flex h-9 items-center">
              <Switch
                {...control}
                checked={draft.late === true}
                onCheckedChange={(checked) => set('late', checked ? true : undefined)}
              />
            </div>
          )}
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit">{t('transactions.filters.apply')}</Button>
        <Button type="button" variant="outline" onClick={clear}>
          {t('transactions.filters.clear')}
        </Button>
      </div>
    </form>
  )
}

/**
 * Transacciones: the tenant's transactions as staff see them, filtered by party, external id,
 * date range, status, instrument, rail and lateness. The filters live in the URL, so a link
 * (from a party's page, say) opens the list already narrowed. Every row opens the detail.
 */
export function TransactionsPage() {
  const { t } = useTranslation()
  const format = useFormat()
  const [searchParams, setSearchParams] = useSearchParams()
  const applied = filtersFromSearch(searchParams)
  const appliedKey = searchOf(applied).toString()
  // The cursor belongs to the filters it was read under: when the URL's filters change by any
  // route (Apply, a link, the sidebar, Back), the list starts again from the first page.
  const [paging, setPaging] = useState<{ key: string; cursor: string | undefined }>({ key: appliedKey, cursor: undefined })
  const cursor = paging.key === appliedKey ? paging.cursor : undefined
  const setCursor = (next: string | undefined) => setPaging({ key: appliedKey, cursor: next })
  const valid = rangeIsValid(applied)
  const transactions = useTransactions(applied, cursor, { enabled: valid })
  const page = transactions.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  // Every filter change starts again from the first page.
  function apply(filters: TransactionFilters) {
    setPaging({ key: searchOf(filters).toString(), cursor: undefined })
    setSearchParams(searchOf(filters))
  }

  const columns: Column<TransactionStaffView>[] = [
    {
      id: 'externalId',
      header: t('transactions.table.externalId'),
      cell: (row) => (
        <Link to={`/transactions/${row.id}`} className="font-mono text-xs underline-offset-4 hover:underline">
          {row.external_id}
        </Link>
      ),
    },
    { id: 'occurredAt', header: t('transactions.table.occurredAt'), cell: (row) => <MexicoCityTimeCell value={row.occurred_at} /> },
    {
      id: 'customer',
      header: t('transactions.table.customer'),
      cell: (row) => (
        <div className="space-y-0.5">
          <Link to={`/parties/${row.party_id}`} title={row.party_id} className="font-mono text-xs underline-offset-4 hover:underline">
            {row.party_id.slice(0, 8)}
          </Link>
          <p className="font-mono text-xs text-muted-foreground">{row.account_ref}</p>
        </div>
      ),
    },
    {
      id: 'instrumentRail',
      header: t('transactions.table.instrumentRail'),
      cell: (row) => `${t(`transactions.instrument.${row.instrument}`)} · ${t(`transactions.rail.${row.rail}`)}`,
    },
    {
      id: 'amount',
      header: t('transactions.table.amount'),
      cell: (row) => <span className="tabular">{formatMinor(row.amount_minor, row.currency, format.locale)}</span>,
      className: 'text-right',
    },
    {
      id: 'status',
      header: t('transactions.table.status'),
      cell: (row) => (
        <div className="flex flex-wrap gap-1">
          <TransactionStatusBadge status={row.status} />
          {row.late && <LateBadge />}
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader title={t('transactions.title')} description={t('transactions.description')} />

      <Card className="animate-fade-in-up">
        <CardHeader>
          {/* Re-mounted when the URL's filters change, so the draft starts from what is applied. */}
          <FilterBar key={appliedKey} applied={applied} onApply={apply} />
        </CardHeader>
        <CardContent>
          {!valid ? (
            <p role="alert" className="text-sm text-destructive">
              {t('transactions.filters.rangeInvalid')}
            </p>
          ) : transactions.isError ? (
            <ErrorBlock error={transactions.error} onRetry={() => void transactions.refetch()} />
          ) : (
            <div className={cn(transactions.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <DataTable
                label={t('transactions.table.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(row) => row.id}
                loading={transactions.isPending}
                empty={t('transactions.empty')}
              />
            </div>
          )}
        </CardContent>
        {valid && showPager && (
          <CardFooter>
            <div className="w-full">
              <CursorPager
                cursor={cursor}
                nextCursor={page?.next_cursor}
                onChange={setCursor}
                busy={transactions.isPlaceholderData}
              />
            </div>
          </CardFooter>
        )}
      </Card>
    </>
  )
}
