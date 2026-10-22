// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { stubPointerCapture } from '@/test/select'
import { RULE_ID, SENTENCE_ES, rule, simulation, template, version } from './fixtures'
import { RulesPage } from './RulesPage'

const text = esMX.rules
const TEMPLATES = 'GET /v1/monitoring/templates'
const RULES = 'GET /v1/monitoring/rules'

const library: FakeRoutes = {
  [TEMPLATES]: () =>
    json([
      template(),
      template({
        code: 'TIER_N1_MONTHLY_OPS',
        kind: 'TIER_LIMIT',
        typology: ['TIER_LIMIT'],
        title: 'Límite mensual de nivel 1',
        sentences: { 'es-MX': 'Dispara cuando los abonos del mes superan el límite del nivel', en: 'Fires when the month exceeds the tier limit' },
      }),
    ]),
}

function setup(role: Role, routes: FakeRoutes = library, path = '/rules/monitoring') {
  return renderApp(
    <Routes>
      <Route path="/rules/monitoring" element={<RulesPage />} />
      <Route path="/rules/monitoring/:ruleId" element={<p>the rule page</p>} />
    </Routes>,
    { path, me: makeMe({ roles: [role] }), routes },
  )
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date('2026-10-22T18:00:00Z'), toFake: ['Date'] })
  stubPointerCapture()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RulesPage', () => {
  it('opens on the library with sentences', async () => {
    setup('ANALYST')

    expect(screen.getByRole('tab', { name: text.tabs.library }).getAttribute('aria-selected')).toBe('true')
    expect(await screen.findByText(SENTENCE_ES)).toBeTruthy()
    const table = screen.getByRole('table', { name: text.table.libraryLabel })
    const [, first] = within(table).getAllByRole('row')
    const cells = within(first as HTMLElement)
    expect(cells.getByText('CASH_STRUCTURING')).toBeTruthy()
    expect(cells.getByText(text.kind.STRUCTURING)).toBeTruthy()
    expect(cells.getByText('STRUCTURING')).toBeTruthy()
    expect(screen.getByText('Dispara cuando los abonos del mes superan el límite del nivel')).toBeTruthy()
    // An analyst reads the library but creates nothing.
    expect(screen.queryByRole('button', { name: /Crear regla/ })).toBeNull()
  })

  it('the tenant tab lists versions with status', async () => {
    const routes: FakeRoutes = {
      ...library,
      [RULES]: () =>
        json({
          items: [
            rule({
              versions: [version({ id: 'v1', status: 'ACTIVE' }), version({ id: 'v2', version: 2, status: 'IN_REVIEW' })],
              active_version_id: 'v1',
              last_simulation: simulation({ rule_version_id: 'v2' }),
            }),
          ],
          next_cursor: null,
        }),
    }
    const { user } = setup('AUDITOR_RO', routes)

    await user.click(screen.getByRole('tab', { name: text.tabs.tenant }))

    const table = await screen.findByRole('table', { name: text.table.tenantLabel })
    await waitFor(() => expect(table.getAttribute('aria-busy')).toBeNull())
    const [, row] = within(table).getAllByRole('row')
    const cells = within(row as HTMLElement)
    expect(cells.getByRole('link', { name: 'CASH_STRUCTURING' }).getAttribute('href')).toBe(`/rules/monitoring/${RULE_ID}`)
    expect(cells.getByText('v2')).toBeTruthy()
    expect(cells.getByText(text.status.IN_REVIEW)).toBeTruthy()
    expect(cells.getByText('v1')).toBeTruthy()
    expect(cells.getByText(/Válida hasta el/)).toBeTruthy()
  })

  it('create posts the template code', async () => {
    const routes: FakeRoutes = { ...library, 'POST /v1/monitoring/rules': () => json(version(), { status: 201 }) }
    const { user, fakeApi } = setup('SENIOR_ANALYST', routes)

    await user.click(await screen.findByRole('button', { name: 'Crear regla desde CASH_STRUCTURING' }))

    expect(await screen.findByText('the rule page')).toBeTruthy()
    const [sent] = fakeApi.calls('POST /v1/monitoring/rules')
    expect(await sent?.json()).toEqual({ template_code: 'CASH_STRUCTURING' })
    expect(sent?.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('narrows the tenant rules by status', async () => {
    const routes: FakeRoutes = { ...library, [RULES]: () => json({ items: [], next_cursor: null }) }
    const { user, fakeApi } = setup('MLRO', routes, '/rules/monitoring?tab=tenant')

    expect(await screen.findByText(text.empty)).toBeTruthy()
    await user.click(screen.getByLabelText(text.filters.status))
    await user.click(await screen.findByRole('option', { name: text.status.APPROVED }))

    await waitFor(() => expect(fakeApi.queries(RULES).at(-1)?.get('status')).toBe('APPROVED'))
  })
})
