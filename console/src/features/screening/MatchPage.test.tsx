// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, noContent, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { matchDetail, matchReveal } from './fixtures'
import { MatchPage } from './MatchPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.screening
const DETAIL = 'GET /v1/screening/matches/m1'
const REVEAL = 'GET /v1/screening/matches/m1/reveal'
const DISPOSE = 'POST /v1/screening/matches/m1/dispositions'
const REVOKE = 'DELETE /v1/screening/whitelist/w1'
const RATIONALE = 'Fecha de nacimiento y nacionalidad distintas al registro.'

function setup(routes: FakeRoutes = {}, roles: Role[] = ['ANALYST']) {
  return renderApp(
    <Routes>
      <Route path="/screening/matches/:matchId" element={<MatchPage />} />
    </Routes>,
    {
      path: '/screening/matches/m1',
      me: makeMe({ roles }),
      routes: {
        [DETAIL]: () => json(matchDetail()),
        [REVEAL]: () => json(matchReveal()),
        [DISPOSE]: () =>
          json(
            { id: 'd1', match_id: 'm1', decision: 'FALSE_POSITIVE', match_status: 'FALSE_POSITIVE', decided_at: '2026-10-20T09:00:00Z' },
            { status: 201 },
          ),
        ...routes,
      },
    },
  )
}

const decisionButton = (decision: keyof typeof text.decision) => screen.getByRole('button', { name: text.decision[decision] })

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('MatchPage', () => {
  it('renders the aligned tokens, qualifiers and thresholds', async () => {
    setup()

    const tokens = await screen.findByRole('table', { name: text.match.explanation.label })
    const rows = within(tokens)
      .getAllByRole('row')
      .slice(1)
      .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent))
    expect(rows).toEqual([
      ['juan', 'juan', text.match.explanation.kinds.EXACT, '1.0000', '0.2100'],
      ['perez', 'peres', text.match.explanation.kinds.PHONETIC, '0.9200', '0.4000'],
    ])
    expect(screen.getByText('lopez')).toBeTruthy()

    const qualifiers = screen.getByRole('table', { name: text.match.qualifiers.label })
    expect(
      within(qualifiers)
        .getAllByRole('row')
        .slice(1)
        .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent)),
    ).toEqual([
      [text.match.qualifiers.attributes.BIRTH_DATE, text.match.qualifiers.outcomes.MATCH, '+0.0500'],
      [text.match.qualifiers.attributes.NATIONALITY, text.match.qualifiers.outcomes.CONFLICT, '-0.1000'],
    ])
    expect(screen.getByText(text.match.rules.codes.ID_MATCH_FLOOR)).toBeTruthy()
    expect(screen.getByText('0.85')).toBeTruthy()
    expect(screen.getByText('0.70')).toBeTruthy()
    expect(screen.getByText('m-2026.10')).toBeTruthy()

    // The subject is masked, and the entry's identifiers too.
    expect(screen.getByText('J. P. L.')).toBeTruthy()
    expect(screen.getByText('RFC ********P8')).toBeTruthy()
    expect(screen.getByText('Juan Peres Lopes')).toBeTruthy()
    expect(document.body.textContent).not.toContain('V12345678')
    expect(document.body.textContent).toContain('5678')
  })

  it('offers only the allowed decisions and explains the blocked ones', async () => {
    setup()
    await screen.findByRole('table', { name: text.match.explanation.label })

    expect(decisionButton('FALSE_POSITIVE').hasAttribute('disabled')).toBe(false)
    expect(decisionButton('POSSIBLE_TRUE_MATCH').hasAttribute('disabled')).toBe(false)
    const trueMatch = decisionButton('TRUE_MATCH')
    const delisted = decisionButton('DELISTED')
    expect(trueMatch.hasAttribute('disabled')).toBe(true)
    expect(delisted.hasAttribute('disabled')).toBe(true)
    const describedBy = (button: HTMLElement) =>
      document.getElementById(button.getAttribute('aria-describedby') ?? '')?.textContent ?? ''
    expect(describedBy(trueMatch)).toContain(text.blocked.STATE)
    expect(describedBy(trueMatch)).toContain(text.blocked.ROLE_REQUIRED)
    expect(describedBy(delisted)).toBe(text.blocked.NOT_DELISTED)
  })

  it('records an allowed decision with its rationale under an Idempotency-Key', async () => {
    const { user, fakeApi } = setup()
    await screen.findByRole('table', { name: text.match.explanation.label })

    await user.click(decisionButton('FALSE_POSITIVE'))
    await user.type(screen.getByLabelText(text.disposition.rationale), RATIONALE)
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))

    await waitFor(() => expect(fakeApi.calls(DISPOSE)).toHaveLength(1))
    const [request] = fakeApi.calls(DISPOSE)
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.json()).toEqual({ decision: 'FALSE_POSITIVE', rationale: RATIONALE })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.disposition.done))
    // The detail is read again, for the new status and actions.
    await waitFor(() => expect(fakeApi.calls(DETAIL).length).toBeGreaterThan(1))
  })

  it('a refused disposition shows its blocked reasons', async () => {
    const { user } = setup({
      [DISPOSE]: () => problem(409, 'DISPOSITION_NOT_ALLOWED', { blocked_reasons: ['SAME_REVIEWER'] }),
    })
    await screen.findByRole('table', { name: text.match.explanation.label })

    await user.click(decisionButton('POSSIBLE_TRUE_MATCH'))
    await user.type(screen.getByLabelText(text.disposition.rationale), RATIONALE)
    await user.click(screen.getByRole('button', { name: text.disposition.submit }))

    expect(await screen.findByText(esMX.errors.DISPOSITION_NOT_ALLOWED)).toBeTruthy()
    expect(screen.getByText(text.disposition.refusedReasons)).toBeTruthy()
    expect(screen.getByText(text.blocked.SAME_REVIEWER).tagName).toBe('LI')
  })

  it('the reveal is a mutation and is forgotten on hide', async () => {
    const { user, fakeApi, queryClient } = setup({
      [DETAIL]: () =>
        json(
          matchDetail({
            dispositions: [
              { id: 'd0', decision: 'POSSIBLE_TRUE_MATCH', decided_by_type: 'STAFF', decided_by: 'u1', decided_at: '2026-10-19T12:00:00Z' },
            ],
          }),
        ),
      [REVEAL]: () => json(matchReveal({ rationales: { d0: 'Escalada por coincidencia de RFC.' } })),
    })
    await screen.findByRole('table', { name: text.match.explanation.label })
    expect(fakeApi.calls(REVEAL)).toHaveLength(0)
    expect(document.body.textContent).not.toContain('Juan Pérez López')

    await user.click(screen.getByRole('button', { name: text.reveal.button }))

    expect(await screen.findByText('Juan Pérez López')).toBeTruthy()
    expect(screen.getByText(/PELJ760510AB8/)).toBeTruthy()
    expect(screen.getByText('Escalada por coincidencia de RFC.')).toBeTruthy()
    expect(screen.getByText(text.reveal.notice)).toBeTruthy()
    expect(fakeApi.calls(REVEAL)).toHaveLength(1)
    // Never a query: nothing a refetch could repeat or a cache could keep.
    expect(queryClient.getQueryCache().getAll().map((query) => query.queryKey)).not.toContainEqual(
      expect.arrayContaining(['reveal']),
    )

    await user.click(screen.getByRole('button', { name: text.reveal.hide }))

    expect(document.body.textContent).not.toContain('Juan Pérez López')
    expect(document.body.textContent).not.toContain('PELJ760510AB8')
    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll().filter((mutation) => mutation.state.data !== undefined)).toEqual([]),
    )
    expect(fakeApi.calls(REVEAL)).toHaveLength(1)
  })

  it('lets the MLRO revoke the whitelist row, after asking', async () => {
    const { user, fakeApi } = setup(
      {
        [DETAIL]: () => json(matchDetail({ status: 'FALSE_POSITIVE', whitelist_id: 'w1', allowed_actions: [] })),
        [REVOKE]: () => noContent(),
      },
      ['MLRO'],
    )

    await user.click(await screen.findByRole('button', { name: text.whitelist.revoke }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: text.whitelist.revoke }))

    await waitFor(() => expect(fakeApi.calls(REVOKE)).toHaveLength(1))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.whitelist.revoked))
  })

  it('offers no revocation or decisions to an auditor', async () => {
    setup({ [DETAIL]: () => json(matchDetail({ whitelist_id: 'w1', allowed_actions: [] })) }, ['AUDITOR_RO'])

    await screen.findByRole('table', { name: text.match.explanation.label })

    expect(screen.queryByRole('button', { name: text.whitelist.revoke })).toBeNull()
    expect(screen.queryByRole('button', { name: text.disposition.submit })).toBeNull()
    expect(screen.getByRole('button', { name: text.reveal.button })).toBeTruthy()
  })
})
