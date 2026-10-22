// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rail, RailSettingResponse, Role } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { RequireAuth, RequireRole } from '@/auth/guards'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { RailsPage } from './RailsPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.rails
const railName = (rail: Rail) => esMX.transactions.rail[rail]
const toggleName = (rail: Rail) => text.toggle.replace('{{rail}}', railName(rail))

function setting(overrides: Partial<RailSettingResponse> = {}): RailSettingResponse {
  return { rail: 'SPEI', fail_mode: 'FAIL_OPEN', explicit: false, changed_by: null, updated_at: null, ...overrides }
}

const RAILS: RailSettingResponse[] = [
  setting(),
  setting({
    rail: 'CASH',
    fail_mode: 'FAIL_CLOSED',
    explicit: true,
    changed_by: '0191d3a2-6b1c-7000-8000-000000000001',
    updated_at: '2026-10-20T18:00:00Z',
  }),
  setting({ rail: 'CRYPTO_ONCHAIN' }),
]

function setup(routes: FakeRoutes = {}, roles: Role[] = ['ADMIN']) {
  return renderApp(<RailsPage />, {
    path: '/admin/rails',
    me: makeMe({ roles }),
    routes: { 'GET /v1/monitoring/rails': () => json(RAILS), ...routes },
  })
}

const table = () => screen.getByRole('table', { name: text.table.label })
async function row(rail: Rail) {
  await waitFor(() => expect(table().getAttribute('aria-busy')).toBeNull())
  return within(within(table()).getByRole('row', { name: new RegExp(railName(rail)) }))
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('RailsPage: the fail mode of each rail', () => {
  it('lists every rail with its mode, and marks the ones that follow the default', async () => {
    setup()

    expect(await screen.findByRole('heading', { name: text.title })).toBeTruthy()
    expect(screen.getByText(text.description)).toBeTruthy()
    const spei = await row('SPEI')
    expect(spei.getByText(text.mode.FAIL_OPEN)).toBeTruthy()
    expect(spei.getByText(text.default)).toBeTruthy()
    expect(spei.getByRole('switch', { name: toggleName('SPEI') }).getAttribute('aria-checked')).toBe('false')
    const cash = await row('CASH')
    expect(cash.getByText(text.mode.FAIL_CLOSED)).toBeTruthy()
    expect(cash.getByText(text.explicit)).toBeTruthy()
    expect(cash.queryByText(text.default)).toBeNull()
    expect(cash.getByRole('switch', { name: toggleName('CASH') }).getAttribute('aria-checked')).toBe('true')
  })

  it('asks before a change, explaining what the decision will answer, then sends the new mode', async () => {
    const { user, fakeApi } = setup({
      'PUT /v1/monitoring/rails/SPEI': () =>
        json(setting({ fail_mode: 'FAIL_CLOSED', explicit: true, updated_at: '2026-10-22T10:00:00Z' })),
    })

    const spei = await row('SPEI')
    await user.click(spei.getByRole('switch', { name: toggleName('SPEI') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(text.confirm.FAIL_CLOSED.description.replaceAll('{{rail}}', railName('SPEI')))).toBeTruthy()
    expect(fakeApi.calls('PUT /v1/monitoring/rails/SPEI')).toHaveLength(0)

    await user.click(within(dialog).getByRole('button', { name: text.confirm.submit }))

    await waitFor(() => expect(fakeApi.calls('PUT /v1/monitoring/rails/SPEI')).toHaveLength(1))
    const [sent] = fakeApi.calls('PUT /v1/monitoring/rails/SPEI')
    expect(await sent?.json()).toEqual({ fail_mode: 'FAIL_CLOSED' })
    // A PUT: the platform keys POSTs only, and repeating it stores the same mode again.
    expect(sent?.headers.has('Idempotency-Key')).toBe(false)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(toast.success).toHaveBeenCalledWith(
      text.changed.replace('{{rail}}', railName('SPEI')).replace('{{mode}}', text.mode.FAIL_CLOSED),
    )
    await waitFor(() => expect(fakeApi.calls('GET /v1/monitoring/rails')).toHaveLength(2))
  })

  it('explains the permissive mode when a restrictive rail is switched back', async () => {
    const { user, fakeApi } = setup({ 'PUT /v1/monitoring/rails/CASH': () => json(setting({ rail: 'CASH', explicit: true })) })

    const cash = await row('CASH')
    await user.click(cash.getByRole('switch', { name: toggleName('CASH') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(text.confirm.FAIL_OPEN.description.replaceAll('{{rail}}', railName('CASH')))).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: text.confirm.submit }))

    await waitFor(() => expect(fakeApi.calls('PUT /v1/monitoring/rails/CASH')).toHaveLength(1))
    expect(await fakeApi.calls('PUT /v1/monitoring/rails/CASH')[0]?.json()).toEqual({ fail_mode: 'FAIL_OPEN' })
  })

  it('sends nothing when the change is cancelled', async () => {
    const { user, fakeApi } = setup()

    const spei = await row('SPEI')
    await user.click(spei.getByRole('switch', { name: toggleName('SPEI') }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: esMX.common.cancel }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(fakeApi.calls('PUT /v1/monitoring/rails/SPEI')).toHaveLength(0)
  })

  it('shows a refusal in the dialog and keeps it open', async () => {
    const { user } = setup({ 'PUT /v1/monitoring/rails/SPEI': () => problem(403, 'ROLE_FORBIDDEN') })

    const spei = await row('SPEI')
    await user.click(spei.getByRole('switch', { name: toggleName('SPEI') }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: text.confirm.submit }))

    expect(await within(dialog).findByText(esMX.errors.ROLE_FORBIDDEN)).toBeTruthy()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('shows a failed load with a retry', async () => {
    setup({ 'GET /v1/monitoring/rails': () => problem(500, 'INTERNAL') })

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
    expect(screen.getByRole('button', { name: esMX.common.retry })).toBeTruthy()
  })
})

describe('the /admin/rails route', () => {
  const guarded = (
    <Routes>
      <Route element={<RequireAuth />}>
        <Route element={<RequireRole roles={ACCESS.rails} />}>
          <Route path="/admin/rails" element={<RailsPage />} />
        </Route>
      </Route>
    </Routes>
  )

  it('opens for ADMIN', async () => {
    renderApp(guarded, {
      path: '/admin/rails',
      me: makeMe({ roles: ['ADMIN'] }),
      routes: { 'GET /v1/monitoring/rails': () => json(RAILS) },
    })

    expect(await screen.findByRole('table', { name: text.table.label })).toBeTruthy()
  })

  it.each<Role>(['AUDITOR_RO', 'MLRO', 'ANALYST', 'SENIOR_ANALYST'])('is closed to %s, which never asks for the rails', async (role) => {
    const { fakeApi } = renderApp(guarded, {
      path: '/admin/rails',
      me: makeMe({ roles: [role] }),
      routes: { 'GET /v1/monitoring/rails': () => json(RAILS) },
    })

    expect(await screen.findByRole('heading', { name: esMX.forbidden.title })).toBeTruthy()
    expect(fakeApi.calls('GET /v1/monitoring/rails')).toHaveLength(0)
    expect(screen.queryByRole('table')).toBeNull()
  })
})
