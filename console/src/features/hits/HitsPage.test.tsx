// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { Severity } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { HIT_PARTY_ID, hitSummary } from './fixtures'
import { HitsPage } from './HitsPage'

const text = esMX.hits
const HITS = 'GET /v1/monitoring/hits'

/** The queue answers by severity: `rows` lists the severities that have a hit. */
function hitsBySeverity(rows: readonly Severity[]): FakeRoutes {
  return {
    [HITS]: (request) => {
      const severity = new URL(request.url).searchParams.get('severity') as Severity | null
      const items =
        severity !== null && rows.includes(severity)
          ? [hitSummary({ id: `h-${severity}`, severity, rule_code: `RULE_${severity}` })]
          : []
      return json({ items, next_cursor: null })
    },
  }
}

function setup(routes: FakeRoutes = hitsBySeverity(['HIGH', 'LOW'])) {
  return renderApp(<HitsPage />, { path: '/monitoring/hits', me: makeMe({ roles: ['AUDITOR_RO'] }), routes })
}

async function loadedTable() {
  const name = text.table.label
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

beforeEach(() => {
  stubPointerCapture()
})

describe('HitsPage', () => {
  it('opens on the highest severity with rows', async () => {
    const { fakeApi } = setup()

    await waitFor(() => expect(screen.getByRole('tab', { name: text.severity.HIGH }).getAttribute('aria-selected')).toBe('true'))
    const table = await loadedTable()

    const [, row] = within(table).getAllByRole('row') as HTMLElement[]
    const cells = within(row as HTMLElement)
    expect(cells.getByRole('link', { name: 'RULE_HIGH' }).getAttribute('href')).toBe('/monitoring/hits/h-HIGH')
    // The party, masked: its initials link to the party, with the masked identifier beneath.
    expect(cells.getByRole('link', { name: 'L.R.' }).getAttribute('href')).toBe(`/parties/${HIT_PARTY_ID}`)
    expect(cells.getByText('•••••••••123')).toBeTruthy()
    expect(cells.getByText('STRUCTURING')).toBeTruthy()
    expect(cells.getByText(text.severity.HIGH)).toBeTruthy()
    expect(cells.getByText(text.outcome.ALERT)).toBeTruthy()
    expect(cells.getByText('3')).toBeTruthy()
    // Every request asks for one severity, never the whole queue at once.
    expect(fakeApi.queries(HITS).every((query) => query.has('severity'))).toBe(true)
    expect(screen.getByRole('tab', { name: text.severity.CRITICAL }).getAttribute('aria-selected')).toBe('false')
  })

  it('opens on the critical tab when nothing has rows', async () => {
    setup(hitsBySeverity([]))

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: text.severity.CRITICAL }).getAttribute('aria-selected')).toBe('true'),
    )
    expect(await screen.findByText(text.empty)).toBeTruthy()
  })

  it('filters by rule and outcome', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()

    await user.type(screen.getByLabelText(text.filters.rule), 'cash_structuring')
    await user.type(screen.getByLabelText(text.filters.typology), 'structuring')
    await user.click(screen.getByRole('button', { name: text.filters.apply }))
    await chooseOption(user, screen.getByLabelText(text.filters.outcome), text.outcome.BLOCK)

    await waitFor(() => expect(fakeApi.queries(HITS).at(-1)?.get('outcome')).toBe('BLOCK'))
    const last = fakeApi.queries(HITS).at(-1)
    expect(last?.get('severity')).toBe('HIGH')
    expect(last?.get('rule_code')).toBe('CASH_STRUCTURING')
    expect(last?.get('typology')).toBe('STRUCTURING')
    expect(last?.get('limit')).toBe('20')

    await chooseOption(user, screen.getByLabelText(text.filters.outcome), text.filters.outcomeAll)
    await waitFor(() => expect(fakeApi.queries(HITS).at(-1)?.has('outcome')).toBe(false))
  })

  it('ages the queue with "older than" and switches severity by tab', async () => {
    const { user, fakeApi } = setup()
    await loadedTable()

    const before = Date.now()
    await chooseOption(user, screen.getByLabelText(text.filters.age), text.filters.age7)
    await waitFor(() => expect(fakeApi.queries(HITS).at(-1)?.get('created_before')).toBeTruthy())
    const createdBefore = Date.parse(fakeApi.queries(HITS).at(-1)?.get('created_before') ?? '')
    expect(before - createdBefore).toBeGreaterThanOrEqual(7 * 86_400_000 - 1000)
    expect(before - createdBefore).toBeLessThanOrEqual(7 * 86_400_000 + 1000)

    await user.click(screen.getByRole('tab', { name: text.severity.LOW }))
    expect(await screen.findByRole('link', { name: 'RULE_LOW' })).toBeTruthy()
    expect(fakeApi.queries(HITS).at(-1)?.get('severity')).toBe('LOW')
    expect(fakeApi.queries(HITS).at(-1)?.get('created_before')).toBeTruthy()
  })
})
