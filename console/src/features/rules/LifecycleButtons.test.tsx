// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VersionResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { RULE_ID, version } from './fixtures'
import { LifecycleButtons } from './LifecycleButtons'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.rules
const VERSION = `/v1/monitoring/rules/${RULE_ID}/versions/1`

function setup(shown: VersionResponse, routes: FakeRoutes = {}) {
  return renderApp(<LifecycleButtons ruleId={RULE_ID} version={shown} />, { me: makeMe({ roles: ['MLRO'] }), routes })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ now: new Date('2026-10-22T18:00:00Z'), toFake: ['Date'] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('LifecycleButtons', () => {
  it('submits at once', async () => {
    const { user, fakeApi } = setup(version({ status: 'SIMULATED', allowed_actions: ['EDIT', 'SIMULATE', 'SUBMIT'] }), {
      [`POST ${VERSION}/submit`]: () => json(version({ status: 'IN_REVIEW' })),
    })

    await user.click(screen.getByRole('button', { name: text.action.SUBMIT }))

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.action.done.SUBMIT))
    expect(fakeApi.calls(`POST ${VERSION}/submit`)[0]?.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('rejects with a reason it says is not stored', async () => {
    const { user, fakeApi } = setup(version({ status: 'IN_REVIEW', allowed_actions: ['APPROVE', 'REJECT'], blocked_reasons: {} }), {
      [`POST ${VERSION}/reject`]: () => json(version()),
    })

    await user.click(screen.getByRole('button', { name: text.action.REJECT }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(text.reject.notStored)).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: text.action.REJECT }))
    expect(within(dialog).getByText(text.reject.reasonRequired)).toBeTruthy()
    expect(fakeApi.calls(`POST ${VERSION}/reject`)).toHaveLength(0)

    await user.type(within(dialog).getByLabelText(text.reject.reason), '  El umbral es demasiado bajo  ')
    await user.click(within(dialog).getByRole('button', { name: text.action.REJECT }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await fakeApi.calls(`POST ${VERSION}/reject`)[0]?.json()).toEqual({ reason: 'El umbral es demasiado bajo' })
  })

  it('activates from a date, for every product unless one is named', async () => {
    const { user, fakeApi } = setup(version({ status: 'APPROVED', allowed_actions: ['ACTIVATE'], blocked_reasons: {} }), {
      [`POST ${VERSION}/activate`]: () => json(version({ status: 'ACTIVE' })),
    })

    await user.click(screen.getByRole('button', { name: text.action.ACTIVATE }))
    const dialog = await screen.findByRole('dialog')
    const date = within(dialog).getByLabelText(text.activate.effectiveFrom) as HTMLInputElement
    expect(date.value).toBe('2026-10-22')
    expect(date.min).toBe('2026-10-22')
    await user.click(within(dialog).getByRole('button', { name: text.action.ACTIVATE }))

    await waitFor(() => expect(fakeApi.calls(`POST ${VERSION}/activate`)).toHaveLength(1))
    expect(await fakeApi.calls(`POST ${VERSION}/activate`)[0]?.json()).toEqual({ effective_from: '2026-10-22', product: null })
  })

  it('retires after a confirmation', async () => {
    const { user, fakeApi } = setup(version({ status: 'ACTIVE', allowed_actions: ['RETIRE'], blocked_reasons: {} }), {
      [`POST ${VERSION}/retire`]: () => json(version({ status: 'RETIRED' })),
    })

    await user.click(screen.getByRole('button', { name: text.action.RETIRE }))
    const dialog = await screen.findByRole('dialog')
    expect(fakeApi.calls(`POST ${VERSION}/retire`)).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: text.action.RETIRE }))

    await waitFor(() => expect(fakeApi.calls(`POST ${VERSION}/retire`)).toHaveLength(1))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.action.done.RETIRE))
  })

  it('says so when no step is open to the user', () => {
    setup(version({ status: 'RETIRED', allowed_actions: [], blocked_reasons: { SUBMIT: ['STATE'], RETIRE: ['STATE'] } }))

    expect(screen.getByText(text.action.none)).toBeTruthy()
  })
})
