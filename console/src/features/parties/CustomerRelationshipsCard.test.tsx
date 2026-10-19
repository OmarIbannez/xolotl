// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CustomerRelationshipStatus } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeUser, renderWithProviders } from '@/test/render'
import { makeMe, renderApp } from '@/test/render-app'
import { CustomerRelationshipsCard } from './CustomerRelationshipsCard'
import { customerRelationship } from './fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.parties.customer
const OPEN = 'POST /v1/relationships'
const CLOSE = 'POST /v1/relationships/r1/close'

function setup(routes: FakeRoutes = {}) {
  return renderApp(<CustomerRelationshipsCard partyId="p1" relationships={[customerRelationship()]} />, {
    me: makeMe({ roles: ['ANALYST'] }),
    routes: {
      [OPEN]: () => json(customerRelationship({ id: 'r2' }), { status: 201 }),
      [CLOSE]: () => json(customerRelationship({ status: 'EXITED', ended_at: '2026-10-19T12:00:00Z' })),
      ...routes,
    },
  })
}

describe('CustomerRelationshipsCard', () => {
  it('opens a relationship with the party, the external reference and the product', async () => {
    const { user, fakeApi } = setup()

    await user.type(await screen.findByLabelText(text.externalRef), 'CTA-0002')
    await user.type(screen.getByLabelText(text.product), 'CREDITO')
    await user.click(screen.getByRole('button', { name: text.open }))

    await waitFor(() => expect(fakeApi.calls(OPEN)).toHaveLength(1))
    const [request] = fakeApi.calls(OPEN)
    expect(await request?.json()).toEqual({ party_id: 'p1', external_ref: 'CTA-0002', product: 'CREDITO' })
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    // The form starts afresh for the next one.
    await waitFor(() => expect(screen.getByLabelText(text.externalRef)).toHaveValue(''))
  })

  it('shows why the API refused to open one', async () => {
    const { user } = setup({ [OPEN]: () => problem(404, 'POLICY_NOT_ASSIGNED') })

    await user.type(await screen.findByLabelText(text.externalRef), 'CTA-0002')
    await user.click(screen.getByRole('button', { name: text.open }))

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.POLICY_NOT_ASSIGNED)
  })

  it('closes a relationship only after the confirm', async () => {
    const { user, fakeApi } = setup()

    await user.click(await screen.findByRole('button', { name: text.close }))
    const dialog = screen.getByRole('dialog', { name: text.close })
    expect(dialog.textContent).toContain(text.confirmClose)
    expect(fakeApi.calls(CLOSE)).toHaveLength(0)

    await user.click(within(dialog).getByRole('button', { name: text.close }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const [request] = fakeApi.calls(CLOSE)
    expect(await request?.json()).toEqual({})
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it.each(['PENDING', 'ACTIVE', 'RESTRICTED', 'BLOCKED', 'EXITED'] as CustomerRelationshipStatus[])(
    'renders the %s badge, and offers no close for an ended relationship only',
    (status) => {
      renderWithProviders(
        <CustomerRelationshipsCard partyId="p1" relationships={[customerRelationship({ status })]} />,
        { user: makeUser({ roles: ['ANALYST'] }) },
      )

      expect(screen.getByText(text.status[status])).toBeTruthy()
      expect(screen.queryByRole('button', { name: text.close }) === null).toBe(status === 'EXITED')
    },
  )

  it('offers neither open nor close to AUDITOR_RO', () => {
    renderWithProviders(<CustomerRelationshipsCard partyId="p1" relationships={[customerRelationship()]} />, {
      user: makeUser({ roles: ['AUDITOR_RO'] }),
    })

    expect(screen.getByText('CTA-0001')).toBeTruthy()
    expect(screen.queryByRole('button', { name: text.close })).toBeNull()
    expect(screen.queryByRole('button', { name: text.open })).toBeNull()
  })

  it('shows an empty list as such', () => {
    renderWithProviders(<CustomerRelationshipsCard partyId="p1" relationships={[]} />)

    expect(screen.getByText(text.empty)).toBeTruthy()
  })
})
