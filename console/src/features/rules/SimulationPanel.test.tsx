// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SimulationResponse, VersionResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { RULE_ID, simulation, version } from './fixtures'
import { SimulationPanel } from './SimulationPanel'

const text = esMX.rules.simulation
const SIMULATE = `POST /v1/monitoring/rules/${RULE_ID}/versions/1/simulate`
const READ = 'GET /v1/monitoring/simulations/s1'

function setup(shown: VersionResponse, routes: FakeRoutes = {}, unsaved = false) {
  return renderApp(<SimulationPanel ruleId={RULE_ID} version={shown} pollMs={10} unsaved={unsaved} />, {
    me: makeMe({ roles: ['SENIOR_ANALYST'] }),
    routes,
  })
}

/** Answers the reads in order, then the last one again. */
function inTurn(answers: SimulationResponse[]): FakeRoutes[string] {
  return () => json(answers.length > 1 ? answers.shift() : answers[0])
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date('2026-10-22T18:00:00Z'), toFake: ['Date'] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SimulationPanel', () => {
  it('runs a six-month simulation and polls until completed', async () => {
    const { user, fakeApi } = setup(version(), {
      [SIMULATE]: () => json({ simulation_id: 's1' }, { status: 202 }),
      [READ]: inTurn([simulation({ status: 'QUEUED' }), simulation({ status: 'RUNNING' }), simulation()]),
    })
    expect(screen.getByText(text.none)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: text.run }))

    const [sent] = fakeApi.calls(SIMULATE)
    expect(await sent?.json()).toEqual({ range_start: '2026-04-23', range_end: '2026-10-22' })
    expect(sent?.headers.get('Idempotency-Key')).toBeTruthy()
    const weeks = await screen.findByRole('table', { name: text.hitsPerWeek })
    expect(fakeApi.calls(READ).length).toBeGreaterThanOrEqual(3)
    const rows = within(weeks).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual(['2026-W4111', '2026-W4232'])
    expect(screen.getByText(text.overlap.existing_hits).nextSibling?.textContent).toBe('2')
    const sample = screen.getByRole('table', { name: text.sample })
    expect(within(sample).getByRole('link', { name: 'party-1' }).getAttribute('href')).toBe('/parties/party-1')
    expect(within(sample).getByRole('link', { name: 'txn-1' }).getAttribute('href')).toBe('/transactions/txn-1')
    expect(screen.getByText(/Válida hasta el/)).toBeTruthy()
    // Finished: no more reads.
    const reads = fakeApi.calls(READ).length
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(fakeApi.calls(READ)).toHaveLength(reads)
  })

  it('shows the age badge', () => {
    const { unmount } = setup(version({ status: 'SIMULATED', simulation: simulation() }))
    expect(screen.getByText(/Válida hasta el/)).toBeTruthy()
    unmount()

    setup(version({ status: 'SIMULATED', simulation: simulation({ valid_until: '2026-10-21' }) }))
    expect(screen.getByText(/Vencida el/)).toBeTruthy()
  })

  it('shows a failed simulation', () => {
    setup(version({ simulation: simulation({ status: 'FAILED', last_error: 'WorkerLost', valid_until: null }) }))

    expect(screen.getByRole('alert').textContent).toContain(text.failed)
    expect(screen.getByText('WorkerLost')).toBeTruthy()
    expect(screen.queryByRole('table', { name: text.hitsPerWeek })).toBeNull()
  })

  it("follows the version's own running simulation without asking for another", async () => {
    const { fakeApi } = setup(version({ allowed_actions: ['EDIT', 'SIMULATE'], simulation: simulation({ status: 'RUNNING' }) }), {
      [READ]: inTurn([simulation({ status: 'RUNNING' }), simulation()]),
    })

    expect(screen.getByRole('status').textContent).toContain(text.running)
    expect(screen.getByRole('button', { name: text.run })).toBeDisabled()
    expect(await screen.findByRole('table', { name: text.hitsPerWeek })).toBeTruthy()
    expect(fakeApi.calls(SIMULATE)).toHaveLength(0)
  })

  it('explains a note and a simulation of another definition', () => {
    setup(version({ definition_hash: 'cd34', simulation: simulation({ notes: ['PROFILE_HISTORY_SHORT'] }) }))

    expect(screen.getByText(text.notes.PROFILE_HISTORY_SHORT)).toBeTruthy()
    expect(screen.getByText(text.otherDefinition)).toBeTruthy()
  })

  it('says so when the simulation cannot be read, rather than wait forever', async () => {
    const { user } = setup(version(), {
      [SIMULATE]: () => json({ simulation_id: 's1' }, { status: 202 }),
      [READ]: () => problem(500, 'INTERNAL'),
    })

    await user.click(screen.getByRole('button', { name: text.run }))

    expect(await screen.findByText(esMX.errors.INTERNAL)).toBeTruthy()
    expect(screen.getByRole('status').textContent).toBe('')
  })

  it('waits for unsaved changes to be saved before simulating', () => {
    setup(version(), {}, true)

    expect(screen.getByRole('button', { name: text.run })).toBeDisabled()
    expect(screen.getByText(text.saveFirst)).toBeTruthy()
  })

  it('cannot run without SIMULATE', async () => {
    setup(version({ allowed_actions: [] }))

    await waitFor(() => expect(screen.getByRole('button', { name: text.run })).toBeDisabled())
  })
})
