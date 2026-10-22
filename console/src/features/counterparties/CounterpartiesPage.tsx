import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { CounterpartyResponse, CounterpartyScreeningStatus } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { useCounterparties } from './queries'
import { StatusBadge } from './StatusBadge'

/** The status tabs, the ones that need a person first. */
const TABS = ['PENDING_REVIEW', 'TRUE_MATCH', 'CLEAR', 'UNSCREENED'] as const satisfies readonly CounterpartyScreeningStatus[]
type Tab = (typeof TABS)[number]

const isTab = (value: string): value is Tab => (TABS as readonly string[]).includes(value)

/** The counterparty's name initials linking to its detail, or its kind when it has no name. */
function CounterpartyLabel({ row }: { row: CounterpartyResponse }) {
  const { t } = useTranslation()
  return (
    <Link to={`/counterparties/${row.id}`} className="font-medium underline-offset-4 hover:underline">
      {row.initials === '' ? t('counterparties.unnamed') : row.initials}
    </Link>
  )
}

/** The masked account or wallet a counterparty is known by, as the list shows it. */
function MaskedValue({ row }: { row: CounterpartyResponse }) {
  const { t } = useTranslation()
  if (row.account_masked !== null) {
    return (
      <span className="font-mono text-xs">
        {t('counterparties.table.accountPrefix')} {row.account_masked}
      </span>
    )
  }
  if (row.wallet_masked !== null) {
    return (
      <span className="font-mono text-xs">
        {row.wallet_network ?? t('counterparties.table.walletPrefix')} {row.wallet_masked}
      </span>
    )
  }
  return <span className="text-muted-foreground">—</span>
}

/**
 * Contrapartes: the tenant's counterparties, masked, with their screening status, by status tab
 * (pending review first) and cursor paging. Each row opens the counterparty's detail.
 */
export function CounterpartiesPage() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<Tab>('PENDING_REVIEW')
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const counterparties = useCounterparties(status, cursor)
  const page = counterparties.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  const columns: Column<CounterpartyResponse>[] = [
    { id: 'name', header: t('counterparties.table.name'), cell: (row) => <CounterpartyLabel row={row} /> },
    { id: 'kind', header: t('counterparties.table.kind'), cell: (row) => t(`counterparties.kind.${row.kind}`) },
    { id: 'country', header: t('counterparties.table.country'), cell: (row) => row.country ?? '—' },
    { id: 'institution', header: t('counterparties.table.institution'), cell: (row) => row.institution ?? '—' },
    { id: 'value', header: t('counterparties.table.value'), cell: (row) => <MaskedValue row={row} /> },
    { id: 'status', header: t('counterparties.table.status'), cell: (row) => <StatusBadge status={row.screening_status} /> },
    { id: 'firstSeen', header: t('counterparties.table.firstSeen'), cell: (row) => <DateTimeCell value={row.first_seen_at} /> },
    { id: 'lastSeen', header: t('counterparties.table.lastSeen'), cell: (row) => <DateTimeCell value={row.last_seen_at} /> },
    {
      id: 'transactions',
      header: t('counterparties.table.transactions'),
      cell: (row) => <span className="tabular">{row.txn_count}</span>,
      className: 'text-right',
    },
  ]

  return (
    <>
      <PageHeader title={t('counterparties.title')} description={t('counterparties.description')} />

      <Card className="animate-fade-in-up">
        <CardHeader>
          <Tabs
            value={status}
            onValueChange={(value) => {
              if (!isTab(value)) return
              setStatus(value)
              setCursor(undefined)
            }}
          >
            <TabsList aria-label={t('counterparties.tabs.label')}>
              {TABS.map((value) => (
                <TabsTrigger key={value} value={value}>
                  {t(`counterparties.tabs.${value}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent>
          {counterparties.isError ? (
            <ErrorBlock error={counterparties.error} onRetry={() => void counterparties.refetch()} />
          ) : (
            <div className={cn(counterparties.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <DataTable
                label={t('counterparties.table.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(row) => row.id}
                loading={counterparties.isPending}
                empty={t('counterparties.empty')}
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
                busy={counterparties.isPlaceholderData}
              />
            </div>
          </CardFooter>
        )}
      </Card>
    </>
  )
}
