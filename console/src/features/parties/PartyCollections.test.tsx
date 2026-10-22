// @vitest-environment jsdom
import { screen, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { PartyStaffResponse, Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { neverScreened } from '@/features/screening/fixtures'
import { fullStaffView, OTHER_PARTY, relationship, RFC, staffView } from './fixtures'
import { PartyPage } from './PartyPage'

const text = esMX.parties
const tabs = text.detail.tabs

function setup(roles: Role[] = ['ANALYST'], view: PartyStaffResponse = fullStaffView()) {
  return renderApp(
    <Routes>
      <Route path="/parties/:partyId" element={<PartyPage />} />
    </Routes>,
    {
      path: '/parties/p1',
      me: makeMe({ roles }),
      routes: {
        'GET /v1/parties/p1': () => json(view),
        'GET /v1/screening/parties/p1': () => json(neverScreened()),
        'GET /v1/transactions': () => json({ items: [], next_cursor: null }),
        'GET /v1/parties/p1/hits': () => json({ items: [], next_cursor: null }),
      },
    },
  )
}

/** Open a tab and return its panel. */
async function openTab(user: ReturnType<typeof setup>['user'], name: string) {
  await user.click(await screen.findByRole('tab', { name }))
  return screen.getByRole('tabpanel', { name })
}

describe('PartyCollections', () => {
  it('lists one row per collection, with masked values only', async () => {
    const { user } = setup()

    const identifiers = await openTab(user, tabs.identifiers)
    expect(within(identifiers).getByText('****01P8')).toBeTruthy()
    expect(within(identifiers).getByText(text.identifierType.RFC)).toBeTruthy()

    const contacts = await openTab(user, tabs.contacts)
    expect(within(contacts).getByText('****a.mx')).toBeTruthy()
    expect(within(contacts).getByText(text.contacts.kind.EMAIL)).toBeTruthy()

    const addresses = await openTab(user, tabs.addresses)
    expect(within(addresses).getByText(text.addresses.type.FISCAL)).toBeTruthy()
    expect(within(addresses).getByText('06600')).toBeTruthy()

    const relationships = await openTab(user, tabs.relationships)
    expect(within(relationships).getByText(text.relationships.type.SHAREHOLDER)).toBeTruthy()
    expect(within(relationships).getByText(text.relationships.direction.outgoing)).toBeTruthy()
    expect(within(relationships).getByRole('link', { name: OTHER_PARTY })).toHaveAttribute('href', `/parties/${OTHER_PARTY}`)
    expect(within(relationships).getByText('25.5000')).toBeTruthy()

    const documents = await openTab(user, tabs.documents)
    expect(within(documents).getByRole('cell', { name: text.documents.type.PROOF_OF_ADDRESS })).toBeTruthy()

    const customer = await openTab(user, tabs.customer)
    expect(within(customer).getByText('CTA-0001')).toBeTruthy()
    expect(within(customer).getByText(text.customer.status.ACTIVE)).toBeTruthy()

    expect(document.body.textContent).not.toContain(RFC)
  })

  it('shows an incoming relationship from the other side', async () => {
    const view = fullStaffView({ relationships: [relationship({ from_party_id: OTHER_PARTY, to_party_id: 'p1' })] })
    const { user } = setup(['ANALYST'], view)

    const panel = await openTab(user, tabs.relationships)
    expect(within(panel).getByText(text.relationships.direction.incoming)).toBeTruthy()
    expect(within(panel).getByRole('link', { name: OTHER_PARTY })).toBeTruthy()
  })

  it('offers the add actions to a writer, opening their dialog', async () => {
    const { user } = setup()

    const panel = await openTab(user, tabs.contacts)
    await user.click(within(panel).getByRole('button', { name: text.contacts.add }))

    expect(screen.getByRole('dialog', { name: text.contacts.add })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: esMX.common.cancel }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('hides every add action and the edit from AUDITOR_RO', async () => {
    const { user } = setup(['AUDITOR_RO'])

    expect(await screen.findByText(text.reveal.title)).toBeTruthy()
    for (const [tab, add] of [
      [tabs.identifiers, text.identifiers.add],
      [tabs.contacts, text.contacts.add],
      [tabs.addresses, text.addresses.add],
      [tabs.relationships, text.relationships.add],
    ] as const) {
      const panel = await openTab(user, tab)
      expect(within(panel).queryByRole('button', { name: add })).toBeNull()
    }
    expect(within(await openTab(user, tabs.documents)).queryByRole('button', { name: text.documents.upload })).toBeNull()
    expect(within(await openTab(user, tabs.customer)).queryByRole('button', { name: text.customer.open })).toBeNull()
    expect(screen.queryByRole('button', { name: text.edit.button })).toBeNull()
  })

  it('says so when a collection is empty', async () => {
    const { user } = setup(['ANALYST'], staffView({ identifiers: [], contacts: [] }))

    expect(within(await openTab(user, tabs.identifiers)).getByText(text.identifiers.empty)).toBeTruthy()
    expect(within(await openTab(user, tabs.contacts)).getByText(text.contacts.empty)).toBeTruthy()
    expect(within(await openTab(user, tabs.addresses)).getByText(text.addresses.empty)).toBeTruthy()
    expect(within(await openTab(user, tabs.relationships)).getByText(text.relationships.empty)).toBeTruthy()
    expect(within(await openTab(user, tabs.documents)).getByText(text.documents.empty)).toBeTruthy()
    expect(within(await openTab(user, tabs.customer)).getByText(text.customer.empty)).toBeTruthy()
  })
})
