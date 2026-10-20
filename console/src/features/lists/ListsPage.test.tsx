// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ListOverview, Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { overview } from './fixtures'
import { ListsPage } from './ListsPage'

const text = esMX.lists

const OFAC = overview({
  code: 'OFAC_SDN',
  title: 'OFAC Specially Designated Nationals and Blocked Persons List',
  jurisdiction: 'US',
  requirement: 'RECOMMENDED',
  active_seq: 7,
  entry_count: 18000,
  stale: true,
})
const SAT = overview({
  code: 'MX_SAT_69B',
  title: 'SAT Artículo 69-B CFF, listado completo',
  jurisdiction: 'MX',
  list_kind: 'FISCAL',
  requirement: 'RECOMMENDED',
  enabled: false,
  failing: true,
  last_error: 'LIST_MASS_REMOVAL: removed 412 of 1003',
})
const EU = overview({
  code: 'EU_FSF',
  title: 'EU Consolidated Financial Sanctions List',
  jurisdiction: 'EU',
  requirement: 'RECOMMENDED',
  active_seq: null,
  published_at: null,
  fetched_at: null,
  entry_count: null,
  stale: true,
  last_success_at: null,
})

function setup(roles: Role[] = ['ADMIN'], { items = [overview(), OFAC, SAT, EU], routes = {} }: { items?: ListOverview[]; routes?: FakeRoutes } = {}) {
  return renderApp(<ListsPage />, {
    path: '/lists',
    me: makeMe({ roles }),
    routes: { 'GET /v1/lists': () => json({ items }), ...routes },
  })
}

const table = () => screen.getByRole('table', { name: text.table.label })
async function row(name: RegExp) {
  await waitFor(() => expect(table().getAttribute('aria-busy')).toBeNull())
  return within(within(table()).getByRole('row', { name }))
}

describe('ListsPage: the subscriptions and their freshness', () => {
  it('shows the loading table, then each list with its requirement, freshness, version and count', async () => {
    let answer: (response: Response) => void = () => undefined
    renderApp(<ListsPage />, {
      path: '/lists',
      routes: { 'GET /v1/lists': () => new Promise<Response>((resolve) => (answer = resolve)) },
    })
    await waitFor(() => expect(table().getAttribute('aria-busy')).toBe('true'))
    answer(json({ items: [overview(), OFAC, EU] }))

    const un = await row(/UN_CONSOLIDATED/)
    expect(un.getByText(text.requirement.MANDATORY)).toBeTruthy()
    expect(un.getByText(text.freshness.FRESH)).toBeTruthy()
    expect(un.getByText('UN Security Council Consolidated List')).toBeTruthy()
    expect(un.getByText('1,003')).toBeTruthy()
    const ofac = await row(/OFAC_SDN/)
    expect(ofac.getByText(text.requirement.RECOMMENDED)).toBeTruthy()
    expect(ofac.getByText(text.freshness.STALE)).toBeTruthy()
    const eu = await row(/EU_FSF/)
    expect(eu.getByText(text.freshness.EMPTY)).toBeTruthy()
  })

  it('shows a failing list with its error code, and the whole error as the badge tooltip', async () => {
    setup()

    const sat = await row(/MX_SAT_69B/)
    const badge = sat.getByText(text.freshness.FAILING)
    expect(sat.getByText('LIST_MASS_REMOVAL')).toBeTruthy()
    expect(badge.closest('[title]')?.getAttribute('title')).toBe('LIST_MASS_REMOVAL: removed 412 of 1003')
  })

  it('links each list to its versions', async () => {
    setup()

    const un = await row(/UN_CONSOLIDATED/)
    expect(un.getByRole('link', { name: 'UN_CONSOLIDATED' }).getAttribute('href')).toBe('/lists/UN_CONSOLIDATED')
  })

  it('shows the empty state when the tenant has no subscription', async () => {
    setup(['ANALYST'], { items: [] })

    expect(await screen.findByText(text.empty)).toBeTruthy()
  })

  it('renders titles as text, never as markup', async () => {
    setup(['ANALYST'], { items: [overview({ title: '<img src=x onerror=alert(1)>' })] })

    const un = await row(/UN_CONSOLIDATED/)
    expect(un.getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
  })

  it('shows a failed load with a retry', async () => {
    setup(['ANALYST'], { routes: { 'GET /v1/lists': () => problem(500, 'INTERNAL') } })

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
  })
})

describe('ListsPage: the subscription toggles', () => {
  it.each(['ANALYST', 'MLRO', 'AUDITOR_RO'] as const)('shows %s no switch, only whether each list is on', async (role) => {
    setup([role])

    const ofac = await row(/OFAC_SDN/)
    expect(ofac.getByText(text.subscription.on)).toBeTruthy()
    expect((await row(/MX_SAT_69B/)).getByText(text.subscription.off)).toBeTruthy()
    expect(screen.queryAllByRole('switch')).toEqual([])
  })

  it('gives a mandatory list a lock and no switch, even for an administrator', async () => {
    setup(['ADMIN'])

    const un = await row(/UN_CONSOLIDATED/)
    expect(un.getByText(text.mandatory)).toBeTruthy()
    expect(un.queryByRole('switch')).toBeNull()
    expect((await row(/OFAC_SDN/)).getByRole('switch')).toBeTruthy()
  })

  it('lets an administrator turn a recommended list off with a PATCH of { enabled: false }', async () => {
    const { user, fakeApi } = setup(['ADMIN'], {
      routes: { 'PATCH /v1/lists/subscriptions/OFAC_SDN': () => json({ ...OFAC, enabled: false }) },
    })

    const toggle = (await row(/OFAC_SDN/)).getByRole('switch', { name: 'OFAC_SDN' })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    await user.click(toggle)

    await waitFor(() => expect(fakeApi.calls('PATCH /v1/lists/subscriptions/OFAC_SDN')).toHaveLength(1))
    const [request] = fakeApi.calls('PATCH /v1/lists/subscriptions/OFAC_SDN')
    expect(await request?.json()).toEqual({ enabled: false })
    // The overview is asked for again so the table shows what the API now holds.
    await waitFor(() => expect(fakeApi.calls('GET /v1/lists')).toHaveLength(2))
  })

  it('turns a disabled recommended list back on', async () => {
    const { user, fakeApi } = setup(['ADMIN'], {
      routes: { 'PATCH /v1/lists/subscriptions/MX_SAT_69B': () => json({ ...SAT, enabled: true }) },
    })

    await user.click((await row(/MX_SAT_69B/)).getByRole('switch', { name: 'MX_SAT_69B' }))

    await waitFor(() => expect(fakeApi.calls('PATCH /v1/lists/subscriptions/MX_SAT_69B')).toHaveLength(1))
    expect(await fakeApi.calls('PATCH /v1/lists/subscriptions/MX_SAT_69B')[0]?.json()).toEqual({ enabled: true })
  })

  it('shows a refused toggle as the translated error', async () => {
    const { user } = setup(['ADMIN'], {
      routes: { 'PATCH /v1/lists/subscriptions/OFAC_SDN': () => problem(409, 'LIST_MANDATORY') },
    })

    await user.click((await row(/OFAC_SDN/)).getByRole('switch', { name: 'OFAC_SDN' }))

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.LIST_MANDATORY)
  })
})
