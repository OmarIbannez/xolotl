import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import type { PartyResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { IdentifierSearch, type IdentifierQuery } from './IdentifierSearch'
import { PartyStatusBadge } from './PartyStatusBadge'
import { useParties, type PartyFilters } from './queries'

/** The tenant's parties, masked: a list with an exact identifier search, and the way to create one. */
export function PartiesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { hasRole } = useAuth()
  const [filters, setFilters] = useState<PartyFilters>({})
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const parties = useParties(filters, cursor)
  const searching = filters.id_type !== undefined

  function search(query: IdentifierQuery | null) {
    setCursor(undefined)
    setFilters(query ?? {})
  }

  const columns: Column<PartyResponse>[] = [
    {
      id: 'name',
      header: t('parties.table.name'),
      cell: (party) => (
        <Link to={`/parties/${party.id}`} className="font-medium underline-offset-4 hover:underline">
          {party.display_name ?? t('parties.table.noName')}
        </Link>
      ),
      className: 'min-w-40 max-w-64 whitespace-normal break-words',
    },
    { id: 'type', header: t('parties.table.type'), cell: (party) => t(`parties.type.${party.party_type}`) },
    {
      id: 'identifiers',
      header: t('parties.table.identifiers'),
      cell: (party) => (
        <ul className="space-y-0.5">
          {party.identifiers.map((identifier) => (
            <li key={identifier.id} className="font-mono text-xs">
              <span className="text-muted-foreground">{t(`parties.identifierType.${identifier.id_type}`)}</span>{' '}
              {identifier.masked_value}
            </li>
          ))}
        </ul>
      ),
    },
    { id: 'status', header: t('parties.table.status'), cell: (party) => <PartyStatusBadge status={party.status} /> },
    { id: 'createdAt', header: t('parties.table.createdAt'), cell: (party) => <DateTimeCell value={party.created_at} /> },
  ]

  const page = parties.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  return (
    <>
      <PageHeader
        title={t('parties.title')}
        description={t('parties.description')}
        actions={
          hasRole(ACCESS.partiesWrite) ? (
            <Button type="button" onClick={() => void navigate('/parties/new')}>
              <Plus aria-hidden />
              {t('parties.new')}
            </Button>
          ) : undefined
        }
      />

      <Card className="animate-fade-in-up">
        <CardHeader>
          <IdentifierSearch onSearch={search} active={searching} />
        </CardHeader>
        <CardContent>
          {parties.isError ? (
            <ErrorBlock error={parties.error} onRetry={() => void parties.refetch()} />
          ) : (
            <div className={cn(parties.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <DataTable
                label={t('parties.table.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(party) => party.id}
                loading={parties.isPending}
                empty={searching ? t('parties.emptyFiltered') : t('parties.empty')}
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
                busy={parties.isPlaceholderData}
              />
            </div>
          </CardFooter>
        )}
      </Card>
    </>
  )
}
