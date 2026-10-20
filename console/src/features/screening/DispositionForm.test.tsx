// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { DispositionForm } from './DispositionForm'
import { matchDetail } from './fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.screening
const DISPOSE = 'POST /v1/screening/matches/m1/dispositions'
const RATIONALE = 'El RFC y la fecha de nacimiento coinciden con el registro.'

function setup(allowed: ('FALSE_POSITIVE' | 'POSSIBLE_TRUE_MATCH' | 'TRUE_MATCH' | 'DELISTED')[] = ['FALSE_POSITIVE', 'POSSIBLE_TRUE_MATCH']) {
  return renderApp(<DispositionForm match={matchDetail({ allowed_actions: allowed, blocked_reasons: {} })} />, {
    me: makeMe({ roles: ['SENIOR_ANALYST'] }),
    routes: {
      [DISPOSE]: () =>
        json(
          { id: 'd2', match_id: 'm1', decision: 'TRUE_MATCH', match_status: 'TRUE_MATCH', decided_at: '2026-10-20T09:00:00Z' },
          { status: 201 },
        ),
    },
  })
}

describe('DispositionForm', () => {
  it('the rationale is required and at least ten characters', async () => {
    const { user, fakeApi } = setup()

    await user.click(screen.getByRole('button', { name: text.decision.FALSE_POSITIVE }))
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))
    expect(await screen.findByText(text.disposition.rationaleShort)).toBeTruthy()

    await user.type(screen.getByLabelText(text.disposition.rationale), 'muy corto')
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))
    expect(screen.getByText(text.disposition.rationaleShort)).toBeTruthy()
    expect(fakeApi.calls(DISPOSE)).toHaveLength(0)

    await user.type(screen.getByLabelText(text.disposition.rationale), '!')
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))
    await waitFor(() => expect(fakeApi.calls(DISPOSE)).toHaveLength(1))
    expect(await fakeApi.calls(DISPOSE)[0]?.json()).toEqual({ decision: 'FALSE_POSITIVE', rationale: 'muy corto!' })
  })

  it('asks for a decision before sending', async () => {
    const { user, fakeApi } = setup()

    await user.type(screen.getByLabelText(text.disposition.rationale), RATIONALE)
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))

    expect(await screen.findByText(text.disposition.chooseDecision)).toBeTruthy()
    expect(fakeApi.calls(DISPOSE)).toHaveLength(0)
  })

  it('true match asks for confirmation', async () => {
    const { user, fakeApi } = setup(['TRUE_MATCH', 'FALSE_POSITIVE'])

    await user.click(screen.getByRole('button', { name: text.decision.TRUE_MATCH }))
    await user.type(screen.getByLabelText(text.disposition.rationale), RATIONALE)
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(text.disposition.confirmTrueMatch)).toBeTruthy()
    expect(fakeApi.calls(DISPOSE)).toHaveLength(0)

    await user.click(within(dialog).getByRole('button', { name: esMX.common.cancel }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(fakeApi.calls(DISPOSE)).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: text.disposition.submit }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: text.decision.TRUE_MATCH }))

    await waitFor(() => expect(fakeApi.calls(DISPOSE)).toHaveLength(1))
    expect(await fakeApi.calls(DISPOSE)[0]?.json()).toEqual({ decision: 'TRUE_MATCH', rationale: RATIONALE })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.disposition.done))
  })

  it('offers nothing to send when no decision is allowed', () => {
    setup([])

    expect(screen.getByText(text.disposition.noneAllowed)).toBeTruthy()
    expect(screen.getByRole('button', { name: text.disposition.submit }).hasAttribute('disabled')).toBe(true)
  })
})
