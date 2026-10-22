// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { BackfillTab } from './BackfillTab'
import { backfillTransaction } from './ingest-fixtures'
import { mexicoCityToday } from './time'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.transactions
const LIST = 'GET /v1/transactions/backfill'
const REQUEST = 'POST /v1/monitoring/backfill'
const PARTY = '0191d3a2-6b1c-7000-8000-0000000000c1'

function setup(roles: Role[], routes: FakeRoutes = {}) {
  return renderApp(<BackfillTab />, {
    path: '/transactions',
    me: makeMe({ roles }),
    routes: {
      [LIST]: () => json({ items: [backfillTransaction()], next_cursor: null }),
      [REQUEST]: () => json({ job_id: 'job-1' }, { status: 202 }),
      ...routes,
    },
  })
}

async function openDialog(user: ReturnType<typeof setup>['user']) {
  await user.click(await screen.findByRole('button', { name: text.backfill.run }))
  return screen.findByRole('dialog')
}

/** Date inputs take their value as typed in ISO form; jsdom has no date picker. */
function setDate(dialog: HTMLElement, label: string, value: string) {
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } })
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('BackfillTab', () => {
  it('lists the rows awaiting a backfill', async () => {
    setup(['ANALYST'])

    await screen.findByText('ext-0003')
    const table = screen.getByRole('table', { name: text.backfill.label })
    expect(within(table).getByText('ext-0003')).toBeTruthy()
    expect(within(table).getByText(text.status.BACKFILL)).toBeTruthy()
  })

  it.each([['ANALYST'], ['SENIOR_ANALYST'], ['AUDITOR_RO']] as const)('offers no backfill to %s', async (role) => {
    setup([role])

    await screen.findByText('ext-0003')
    expect(screen.queryByRole('button', { name: text.backfill.run })).toBeNull()
  })

  it.each([['MLRO'], ['ADMIN']] as const)('offers the backfill to %s', async (role) => {
    setup([role])

    expect(await screen.findByRole('button', { name: text.backfill.run })).toBeTruthy()
  })

  it('posts from, to and the party id with an Idempotency-Key, then shows the toast', async () => {
    const { user, fakeApi } = setup(['MLRO'])
    const dialog = await openDialog(user)

    setDate(dialog, text.backfill.from, '2026-11-01')
    setDate(dialog, text.backfill.to, '2026-11-30')
    await user.type(within(dialog).getByLabelText(text.backfill.partyId), PARTY)
    await user.click(within(dialog).getByRole('button', { name: text.backfill.submit }))

    await waitFor(() => expect(fakeApi.calls(REQUEST)).toHaveLength(1))
    const [request] = fakeApi.calls(REQUEST)
    expect(await request?.json()).toEqual({ from: '2026-11-01', to: '2026-11-30', party_id: PARTY })
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.backfill.requested))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('leaves the party out when none is given', async () => {
    const { user, fakeApi } = setup(['ADMIN'])
    const dialog = await openDialog(user)

    setDate(dialog, text.backfill.from, '2026-11-01')
    setDate(dialog, text.backfill.to, '2026-11-01')
    await user.click(within(dialog).getByRole('button', { name: text.backfill.submit }))

    await waitFor(() => expect(fakeApi.calls(REQUEST)).toHaveLength(1))
    expect(await fakeApi.calls(REQUEST)[0]?.json()).toEqual({ from: '2026-11-01', to: '2026-11-01' })
  })

  it('checks the range and the party id before sending', async () => {
    const { user, fakeApi } = setup(['MLRO'])
    const dialog = await openDialog(user)

    await user.click(within(dialog).getByRole('button', { name: text.backfill.submit }))
    expect(within(dialog).getAllByText(esMX.validation.required)).toHaveLength(2)

    setDate(dialog, text.backfill.from, '2026-11-30')
    setDate(dialog, text.backfill.to, '2026-11-01')
    await user.type(within(dialog).getByLabelText(text.backfill.partyId), 'not-a-uuid')
    await user.click(within(dialog).getByRole('button', { name: text.backfill.submit }))

    expect(within(dialog).getByText(text.backfill.rangeOrder)).toBeTruthy()
    expect(within(dialog).getByText(esMX.validation.uuid)).toBeTruthy()
    expect(fakeApi.calls(REQUEST)).toEqual([])
  })

  it('shows a refused range as its translated error', async () => {
    const { user } = setup(['MLRO'], { [REQUEST]: () => problem(422, 'BACKFILL_RANGE_INVALID') })
    const dialog = await openDialog(user)

    setDate(dialog, text.backfill.from, '2026-11-01')
    setDate(dialog, text.backfill.to, '2026-11-30')
    await user.click(within(dialog).getByRole('button', { name: text.backfill.submit }))

    expect(await within(dialog).findByText(esMX.errors.BACKFILL_RANGE_INVALID)).toBeTruthy()
  })

  it('lets no date be picked after today in Mexico City', async () => {
    const { user } = setup(['MLRO'])
    const dialog = await openDialog(user)

    expect(within(dialog).getByLabelText(text.backfill.from).getAttribute('max')).toBe(mexicoCityToday())
    expect(within(dialog).getByLabelText(text.backfill.to).getAttribute('max')).toBe(mexicoCityToday())
  })
})
