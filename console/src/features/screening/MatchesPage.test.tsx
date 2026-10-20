// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { maskedSubject, matchSummary } from './fixtures'
import { MatchesPage } from './MatchesPage'

const text = esMX.screening
const MATCHES = 'GET /v1/screening/matches'

function setup(routes: FakeRoutes = {}) {
  return renderApp(<MatchesPage />, {
    path: '/screening/matches',
    me: makeMe({ roles: ['ANALYST'] }),
    routes: {
      [MATCHES]: (request) =>
        new URL(request.url).searchParams.get('band') === 'LOW_CONFIDENCE'
          ? json({ items: [matchSummary({ id: 'm2', band: 'LOW_CONFIDENCE', score: '0.7400' })], next_cursor: null })
          : json({ items: [matchSummary()], next_cursor: null }),
      ...routes,
    },
  })
}

async function loadedTable() {
  const name = text.matches.table.label
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

beforeEach(() => {
  stubPointerCapture()
})

describe('MatchesPage', () => {
  it('opens on the ALERT tab with OPEN findings', async () => {
    const { fakeApi } = setup()

    const table = await loadedTable()

    expect(screen.getByRole('tab', { name: text.matches.tabs.ALERT }).getAttribute('aria-selected')).toBe('true')
    const [query] = fakeApi.queries(MATCHES)
    expect(query?.get('band')).toBe('ALERT')
    expect(query?.get('status')).toBe('OPEN')
    expect(query?.get('limit')).toBe('20')
    const [, row] = within(table).getAllByRole('row')
    expect(within(row as HTMLElement).getByRole('link', { name: 'J. P. L.' }).getAttribute('href')).toBe('/screening/matches/m1')
    expect(within(row as HTMLElement).getByText(text.matches.table.party)).toBeTruthy()
    expect(within(row as HTMLElement).getByText('RFC ********P8')).toBeTruthy()
    expect(within(row as HTMLElement).queryByText('s1')).toBeNull()
    expect(within(row as HTMLElement).getByText('OFAC_SDN')).toBeTruthy()
    expect(within(row as HTMLElement).getByText(text.band.ALERT)).toBeTruthy()
    expect(within(row as HTMLElement).getByText(text.status.OPEN)).toBeTruthy()
  })

  it('switching tab requests LOW_CONFIDENCE', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()

    await user.click(screen.getByRole('tab', { name: text.matches.tabs.LOW_CONFIDENCE }))

    await waitFor(() => expect(fakeApi.queries(MATCHES).at(-1)?.get('band')).toBe('LOW_CONFIDENCE'))
    expect(await screen.findByText('0.7400')).toBeTruthy()
    expect(fakeApi.queries(MATCHES).at(-1)?.get('status')).toBe('OPEN')
  })

  it('shows the delisting suggestion', async () => {
    setup({
      [MATCHES]: () => json({ items: [matchSummary({ suggested_disposition: 'DELISTED' })], next_cursor: null }),
    })

    const table = await loadedTable()

    expect(within(table).getByText(text.suggested.DELISTED)).toBeTruthy()
  })

  it('shows an ad hoc subject by its initials and a purged party by its origin', async () => {
    setup({
      [MATCHES]: () =>
        json({
          items: [
            matchSummary({
              id: 'm3',
              party_id: null,
              subject: maskedSubject({ kind: 'AD_HOC', party_id: null, identifiers: [] }),
            }),
            matchSummary({ id: 'm4', party_id: 'p2', subject: maskedSubject({ party_id: 'p2', initials: '', identifiers: [] }) }),
          ],
          next_cursor: null,
        }),
    })

    const table = await loadedTable()

    const [, adHoc, purged] = within(table).getAllByRole('row')
    expect(within(adHoc as HTMLElement).getByRole('link', { name: 'J. P. L.' }).getAttribute('href')).toBe('/screening/matches/m3')
    expect(within(adHoc as HTMLElement).getByText(text.matches.table.adHoc)).toBeTruthy()
    expect(within(adHoc as HTMLElement).queryByText(/RFC/)).toBeNull()
    expect(within(purged as HTMLElement).getByRole('link', { name: text.matches.table.party }).getAttribute('href')).toBe(
      '/screening/matches/m4',
    )
  })

  it('sends the status, list kind, list code and age filters', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()

    await chooseOption(user, screen.getByLabelText(text.matches.filters.status), text.status.ESCALATED)
    await chooseOption(user, screen.getByLabelText(text.matches.filters.listKind), esMX.privateLists.kind.BLOCKED)
    await user.type(screen.getByLabelText(text.matches.filters.listCode), 'mx_lpb')
    await user.click(screen.getByRole('button', { name: text.matches.filters.apply }))
    const before = Date.now()
    await chooseOption(user, screen.getByLabelText(text.matches.filters.age), text.matches.filters.age7)

    await waitFor(() => expect(fakeApi.queries(MATCHES).at(-1)?.get('created_before')).toBeTruthy())
    const last = fakeApi.queries(MATCHES).at(-1)
    expect(last?.get('status')).toBe('ESCALATED')
    expect(last?.get('list_kind')).toBe('BLOCKED')
    expect(last?.get('list_code')).toBe('MX_LPB')
    const createdBefore = Date.parse(last?.get('created_before') ?? '')
    expect(before - createdBefore).toBeGreaterThanOrEqual(7 * 86_400_000 - 1000)
    expect(before - createdBefore).toBeLessThanOrEqual(7 * 86_400_000 + 1000)

    await chooseOption(user, screen.getByLabelText(text.matches.filters.status), text.matches.filters.statusAll)
    await waitFor(() => expect(fakeApi.queries(MATCHES).at(-1)?.has('status')).toBe(false))
  })
})
