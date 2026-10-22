// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role, VersionResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { stubPointerCapture } from '@/test/select'
import { RULE_ID, SENTENCE_ES, rule, simulation, version } from './fixtures'
import { RulePage } from './RulePage'

const text = esMX.rules
const RULE = `GET /v1/monitoring/rules/${RULE_ID}`
const VERSION = `/v1/monitoring/rules/${RULE_ID}/versions/1`

function setup(versions: VersionResponse[], routes: FakeRoutes = {}, role: Role = 'SENIOR_ANALYST') {
  return renderApp(
    <Routes>
      <Route path="/rules/monitoring/:ruleId" element={<RulePage />} />
    </Routes>,
    {
      path: `/rules/monitoring/${RULE_ID}`,
      me: makeMe({ roles: [role] }),
      routes: { [RULE]: () => json(rule({ versions })), ...routes },
    },
  )
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date('2026-10-22T18:00:00Z'), toFake: ['Date'] })
  stubPointerCapture()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RulePage', () => {
  it('the editor shows bounds and their source', async () => {
    setup([version()])

    expect(await screen.findByRole('heading', { name: 'CASH_STRUCTURING', level: 1 })).toBeTruthy()
    expect(screen.getByText(SENTENCE_ES)).toBeTruthy()
    const threshold = screen.getByLabelText('threshold (USD)')
    expect((threshold as HTMLInputElement).value).toBe('5000')
    expect(screen.getByText(`${text.editor.decimalHint} mín. 1000 · solo puede bajar · límites de template CASH_STRUCTURING v1`)).toBeTruthy()
    expect(screen.getByText(`${text.editor.decimalHint} mín. 0.50 · máx. 0.95 · límites de template CASH_STRUCTURING v1`)).toBeTruthy()
    const minCount = screen.getByLabelText('min_count') as HTMLInputElement
    expect(minCount.type).toBe('number')
    expect(minCount.min).toBe('2')
  })

  it('an out-of-bounds value maps to its field', async () => {
    const { user, fakeApi } = setup([version()], {
      [`PATCH ${VERSION}`]: () =>
        problem(422, 'RULE_PARAM_OUT_OF_BOUNDS', {
          errors: [{ loc: ['params', 'threshold'], msg: 'threshold 6000 is above …', type: 'RULE_PARAM_OUT_OF_BOUNDS' }],
        }),
    })
    const threshold = await screen.findByLabelText('threshold (USD)')

    await user.clear(threshold)
    await user.type(threshold, '6,000')
    await user.click(screen.getByRole('button', { name: text.editor.save }))

    await waitFor(() => expect(threshold.getAttribute('aria-invalid')).toBe('true'))
    expect(screen.getByText('Fuera de los límites: mín. 1000 · solo puede bajar · límites de template CASH_STRUCTURING v1')).toBeTruthy()
    const [sent] = fakeApi.calls(`PATCH ${VERSION}`)
    const body = (await sent?.json()) as { params: Record<string, unknown> }
    expect(body.params.threshold).toBe('6000')
    // The server's English text is never shown.
    expect(screen.queryByText(/is above/)).toBeNull()
  })

  it('offers only the allowed actions and explains the blocked ones', async () => {
    setup([
      version({
        status: 'SIMULATED',
        simulation: simulation(),
        allowed_actions: ['EDIT', 'SIMULATE'],
        blocked_reasons: { SUBMIT: ['NOT_AUTHOR'], APPROVE: ['STATE', 'SAME_AUTHOR'], REJECT: ['STATE'], ACTIVATE: ['STATE'], RETIRE: ['STATE'] },
      }),
    ])

    const actions = await screen.findByRole('group', { name: text.action.label })
    const submit = within(actions).getByRole('button', { name: text.action.SUBMIT })
    expect(submit).toBeDisabled()
    expect(within(actions).getByText(text.blocked.NOT_AUTHOR)).toBeTruthy()
    // Refused for the version's state: another stage's step, left out.
    for (const action of ['APPROVE', 'REJECT', 'ACTIVATE', 'RETIRE'] as const) {
      expect(within(actions).queryByRole('button', { name: text.action[action] })).toBeNull()
    }
    expect(screen.getByRole('button', { name: text.simulation.run })).toBeEnabled()
  })

  it('approve as the author shows SAME_AUTHOR', async () => {
    setup(
      [
        version({
          status: 'IN_REVIEW',
          simulation: simulation(),
          allowed_actions: ['REJECT'],
          blocked_reasons: { SUBMIT: ['STATE'], APPROVE: ['SAME_AUTHOR'], ACTIVATE: ['STATE'], RETIRE: ['STATE'] },
        }),
      ],
      {},
      'MLRO',
    )

    const actions = await screen.findByRole('group', { name: text.action.label })
    expect(within(actions).getByRole('button', { name: text.action.APPROVE })).toBeDisabled()
    expect(within(actions).getByText('El aprobador debe ser distinto del autor y de quien editó la versión.')).toBeTruthy()
    expect(within(actions).getByRole('button', { name: text.action.REJECT })).toBeEnabled()
    // Nothing is editable in review.
    expect(screen.getByLabelText('threshold (USD)')).toBeDisabled()
    expect(screen.getByRole('button', { name: text.simulation.run })).toBeDisabled()
  })

  it('a refused approval says why', async () => {
    const { user } = setup(
      [version({ status: 'IN_REVIEW', simulation: simulation(), allowed_actions: ['APPROVE', 'REJECT'], blocked_reasons: {} })],
      { [`POST ${VERSION}/approve`]: () => problem(409, 'RULE_TRANSITION_NOT_ALLOWED', { blocked_reasons: ['SIMULATION_STALE'] }) },
      'MLRO',
    )

    await user.click(await screen.findByRole('button', { name: text.action.APPROVE }))

    expect(await screen.findByText(esMX.errors.RULE_TRANSITION_NOT_ALLOWED)).toBeTruthy()
    expect(screen.getByText(text.blocked.SIMULATION_STALE)).toBeTruthy()
  })

  it("another version's refusal does not follow the user to this one", async () => {
    const { user } = setup(
      [
        version({ id: 'v1', status: 'IN_REVIEW', allowed_actions: ['APPROVE'], blocked_reasons: {} }),
        version({ id: 'v2', version: 2, status: 'IN_REVIEW', allowed_actions: ['APPROVE'], blocked_reasons: {} }),
      ],
      {
        [`POST /v1/monitoring/rules/${RULE_ID}/versions/2/approve`]: () =>
          problem(409, 'RULE_TRANSITION_NOT_ALLOWED', { blocked_reasons: ['SIMULATION_STALE'] }),
      },
      'MLRO',
    )

    await user.click(await screen.findByRole('button', { name: text.action.APPROVE }))
    expect(await screen.findByText(text.blocked.SIMULATION_STALE)).toBeTruthy()
    await user.click(within(screen.getByRole('list', { name: text.versions.title })).getByRole('button', { name: /v1/ }))

    expect(screen.queryByText(text.blocked.SIMULATION_STALE)).toBeNull()
  })

  it('keeps unsaved edits when the version moves on, and holds the simulation until they are saved', async () => {
    let shown = version()
    const { user, queryClient } = setup([], { [RULE]: () => json(rule({ versions: [shown] })) })
    const ratio = await screen.findByLabelText('near_ratio')
    await user.clear(ratio)
    await user.type(ratio, '0.85')

    expect(screen.getByRole('button', { name: text.simulation.run })).toBeDisabled()
    shown = version({ status: 'SIMULATED', allowed_actions: ['EDIT', 'SIMULATE', 'SUBMIT'] })
    await queryClient.invalidateQueries()
    expect(await screen.findByRole('button', { name: text.action.SUBMIT })).toBeTruthy()

    expect((screen.getByLabelText('near_ratio') as HTMLInputElement).value).toBe('0.85')
  })

  it.each([
    ['threshold (USD)', '5,000'],
    ['min_count', '03'],
    [esMX.rules.editor.segmentField.tier, ''],
  ])('a save the server finds unchanged leaves nothing unsaved (%s typed as "%s")', async (label, typed) => {
    const { user, fakeApi } = setup([version()], { [`PATCH ${VERSION}`]: () => json(version()) })
    const field = await screen.findByLabelText(label)

    await user.clear(field)
    if (typed !== '') await user.type(field, typed)
    if (label === esMX.rules.editor.segmentField.tier) await user.type(field, 'n1')
    await user.click(screen.getByRole('button', { name: text.editor.save }))

    await waitFor(() => expect(fakeApi.calls(`PATCH ${VERSION}`)).toHaveLength(1))
    await waitFor(() => expect(screen.getByRole('button', { name: text.simulation.run })).toBeEnabled())
    expect(screen.queryByText(text.simulation.saveFirst)).toBeNull()
  })

  it('a form that only respells the stored values is not unsaved', async () => {
    const { user } = setup([version()])
    const threshold = await screen.findByLabelText('threshold (USD)')

    await user.clear(threshold)
    await user.type(threshold, '5,000')

    expect(screen.getByRole('button', { name: text.simulation.run })).toBeEnabled()
    await user.type(threshold, '1')
    expect(screen.getByRole('button', { name: text.simulation.run })).toBeDisabled()
  })

  it('lists the versions newest first and opens the one chosen', async () => {
    const { user } = setup([
      version({ id: 'v1', status: 'ACTIVE', allowed_actions: [], blocked_reasons: {} }),
      version({ id: 'v2', version: 2, sentence: 'La versión 2' }),
    ])

    const list = await screen.findByRole('list', { name: text.versions.title })
    const items = within(list).getAllByRole('button')
    expect(items.map((item) => item.textContent)).toEqual([expect.stringContaining('v2'), expect.stringContaining('v1')])
    expect(screen.getByText('La versión 2')).toBeTruthy()

    await user.click(within(list).getByRole('button', { name: /v1/ }))

    expect(screen.getByText('Versión 1')).toBeTruthy()
    expect(screen.getByText(SENTENCE_ES)).toBeTruthy()
  })

  it('a writer starts a new version from the latest', async () => {
    const { user, fakeApi } = setup([version({ status: 'ACTIVE', allowed_actions: [], blocked_reasons: {} })], {
      [`POST /v1/monitoring/rules/${RULE_ID}/versions`]: () => json(version({ id: 'v2', version: 2 }), { status: 201 }),
    })

    await user.click(await screen.findByRole('button', { name: text.versions.create }))

    await waitFor(() => expect(fakeApi.calls(`POST /v1/monitoring/rules/${RULE_ID}/versions`)).toHaveLength(1))
    const [sent] = fakeApi.calls(`POST /v1/monitoring/rules/${RULE_ID}/versions`)
    expect(await sent?.json()).toEqual({})
    expect(sent?.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('an unknown rule is not found', async () => {
    setup([], { [RULE]: () => problem(404, 'NOT_FOUND') })

    expect(await screen.findByText(esMX.notFound.title)).toBeTruthy()
  })
})
