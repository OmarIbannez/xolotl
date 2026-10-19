import { Plus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type {
  AddressResponse,
  MaskedContact,
  MaskedIdentifier,
  PartyRelationshipResponse,
  PartyStaffResponse,
} from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useFormat } from '@/i18n/use-locale'
import { AddAddressDialog } from './AddAddressDialog'
import { AddContactDialog } from './AddContactDialog'
import { AddIdentifierDialog } from './AddIdentifierDialog'
import { AddRelationshipDialog } from './AddRelationshipDialog'
import { CustomerRelationshipsCard } from './CustomerRelationshipsCard'
import { calendarDay } from './dates'
import { DocumentsCard } from './DocumentsCard'

const TABS = ['identifiers', 'contacts', 'addresses', 'relationships', 'documents', 'customer'] as const
type Tab = (typeof TABS)[number]
type Adding = 'identifiers' | 'contacts' | 'addresses' | 'relationships'

/** A calendar day of the API, or a dash. */
function Day({ value }: { value: string | null }) {
  const format = useFormat()
  return value === null ? '—' : <time dateTime={value}>{format.date(calendarDay(value))}</time>
}

/** One collection: its add button (writers only) over its table. */
function Section({ addLabel, onAdd, children }: { addLabel: string; onAdd: (() => void) | undefined; children: ReactNode }) {
  return (
    <div className="space-y-3">
      {onAdd && (
        <div className="flex justify-end">
          <Button type="button" variant="outline" size="sm" onClick={onAdd}>
            <Plus aria-hidden />
            {addLabel}
          </Button>
        </div>
      )}
      {children}
    </div>
  )
}

/**
 * The detail page's collections, one tab each, over the masked staff view: identifiers, contacts,
 * addresses (codes only; the lines come with the reveal), relationships to other parties,
 * documents and customer relationships. Writers get an add action per tab.
 */
export function PartyCollections({ party }: { party: PartyStaffResponse }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const canWrite = hasRole(ACCESS.partiesWrite)
  const [adding, setAdding] = useState<Adding | null>(null)
  const addFor = (collection: Adding) => (canWrite ? () => setAdding(collection) : undefined)
  const close = () => setAdding(null)

  const identifierColumns: Column<MaskedIdentifier>[] = [
    { id: 'type', header: t('parties.identifiers.type'), cell: (row) => t(`parties.identifierType.${row.id_type}`) },
    { id: 'value', header: t('parties.identifiers.value'), cell: (row) => <span className="font-mono">{row.masked_value}</span> },
    { id: 'country', header: t('parties.identifiers.country'), cell: (row) => row.country_code },
    { id: 'validFrom', header: t('parties.identifiers.validFrom'), cell: (row) => <Day value={row.valid_from} /> },
    { id: 'validTo', header: t('parties.identifiers.validTo'), cell: (row) => <Day value={row.valid_to} /> },
  ]
  const contactColumns: Column<MaskedContact>[] = [
    { id: 'kind', header: t('parties.contacts.kindLabel'), cell: (row) => t(`parties.contacts.kind.${row.kind}`) },
    { id: 'value', header: t('parties.contacts.value'), cell: (row) => <span className="font-mono">{row.masked_value}</span> },
    {
      id: 'primary',
      header: t('parties.contacts.primary'),
      cell: (row) => (row.is_primary ? <Badge variant="info">{t('parties.contacts.primary')}</Badge> : null),
    },
  ]
  const addressColumns: Column<AddressResponse>[] = [
    { id: 'type', header: t('parties.addresses.typeLabel'), cell: (row) => t(`parties.addresses.type.${row.address_type}`) },
    { id: 'country', header: t('parties.addresses.country'), cell: (row) => row.country_code },
    {
      id: 'codes',
      header: `${t('parties.addresses.adminArea')} / ${t('parties.addresses.municipality')}`,
      cell: (row) => <span className="font-mono">{[row.admin_area_code, row.municipality_code].map((code) => code ?? '—').join(' / ')}</span>,
    },
    { id: 'postalCode', header: t('parties.addresses.postalCode'), cell: (row) => <span className="font-mono">{row.postal_code ?? '—'}</span> },
    { id: 'validFrom', header: t('parties.addresses.validFrom'), cell: (row) => <Day value={row.valid_from} /> },
    { id: 'validTo', header: t('parties.addresses.validTo'), cell: (row) => <Day value={row.valid_to} /> },
  ]
  const relationshipColumns: Column<PartyRelationshipResponse>[] = [
    {
      id: 'direction',
      header: t('parties.relationships.directionLabel'),
      cell: (row) =>
        row.from_party_id === party.id
          ? t('parties.relationships.direction.outgoing')
          : t('parties.relationships.direction.incoming'),
    },
    {
      id: 'other',
      header: t('parties.relationships.from'),
      cell: (row) => {
        const other = row.from_party_id === party.id ? row.to_party_id : row.from_party_id
        return (
          <Link to={`/parties/${other}`} className="font-mono text-xs break-all underline-offset-4 hover:underline">
            {other}
          </Link>
        )
      },
    },
    { id: 'type', header: t('parties.relationships.typeLabel'), cell: (row) => t(`parties.relationships.type.${row.rel_type}`) },
    {
      id: 'ownership',
      header: t('parties.relationships.ownership'),
      cell: (row) => row.ownership_pct ?? '—',
      className: 'text-right tabular-nums',
    },
    { id: 'validFrom', header: t('parties.relationships.validFrom'), cell: (row) => <Day value={row.valid_from} /> },
    { id: 'validTo', header: t('parties.relationships.validTo'), cell: (row) => <Day value={row.valid_to} /> },
  ]

  const panels: Record<Tab, ReactNode> = {
    identifiers: (
      <Section addLabel={t('parties.identifiers.add')} onAdd={addFor('identifiers')}>
        <DataTable
          label={t('parties.detail.tabs.identifiers')}
          columns={identifierColumns}
          rows={party.identifiers}
          getRowId={(row) => row.id}
          empty={t('parties.identifiers.empty')}
        />
      </Section>
    ),
    contacts: (
      <Section addLabel={t('parties.contacts.add')} onAdd={addFor('contacts')}>
        <DataTable
          label={t('parties.detail.tabs.contacts')}
          columns={contactColumns}
          rows={party.contacts}
          getRowId={(row) => row.id}
          empty={t('parties.contacts.empty')}
        />
      </Section>
    ),
    addresses: (
      <Section addLabel={t('parties.addresses.add')} onAdd={addFor('addresses')}>
        <p className="text-xs text-muted-foreground">{t('parties.addresses.linesHidden')}</p>
        <DataTable
          label={t('parties.detail.tabs.addresses')}
          columns={addressColumns}
          rows={party.addresses}
          getRowId={(row) => row.id}
          empty={t('parties.addresses.empty')}
        />
      </Section>
    ),
    relationships: (
      <Section addLabel={t('parties.relationships.add')} onAdd={addFor('relationships')}>
        <DataTable
          label={t('parties.detail.tabs.relationships')}
          columns={relationshipColumns}
          rows={party.relationships}
          getRowId={(row) => row.id}
          empty={t('parties.relationships.empty')}
        />
      </Section>
    ),
    documents: <DocumentsCard partyId={party.id} documents={party.documents} />,
    customer: <CustomerRelationshipsCard partyId={party.id} relationships={party.customer_relationships} />,
  }

  return (
    <Card className="animate-fade-in-up">
      <CardContent>
        <Tabs defaultValue="identifiers">
          <TabsList aria-label={t('parties.detail.tabs.label')} className="flex-wrap">
            {TABS.map((tab) => (
              <TabsTrigger key={tab} value={tab}>
                {t(`parties.detail.tabs.${tab}`)}
              </TabsTrigger>
            ))}
          </TabsList>
          {TABS.map((tab) => (
            <TabsContent key={tab} value={tab} className="pt-2">
              {panels[tab]}
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
      {adding === 'identifiers' && <AddIdentifierDialog partyId={party.id} onClose={close} />}
      {adding === 'contacts' && <AddContactDialog partyId={party.id} onClose={close} />}
      {adding === 'addresses' && <AddAddressDialog partyId={party.id} onClose={close} />}
      {adding === 'relationships' && <AddRelationshipDialog partyId={party.id} onClose={close} />}
    </Card>
  )
}
