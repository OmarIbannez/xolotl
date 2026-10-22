// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { CounterpartyPage } from './CounterpartyPage'
import { counterpartyDetail, revealedCounterparty } from './fixtures'

const text = esMX.counterparties
const DETAIL = 'GET /v1/counterparties/c1'
const REVEAL = 'GET /v1/counterparties/c1/reveal'

function setup(routes: FakeRoutes = {}, roles: Role[] = ['ANALYST']) {
  return renderApp(
    <Routes>
      <Route path="/counterparties/:counterpartyId" element={<CounterpartyPage />} />
    </Routes>,
    {
      path: '/counterparties/c1',
      me: makeMe({ roles }),
      routes: {
        [DETAIL]: () => json(counterpartyDetail()),
        [REVEAL]: () => json(revealedCounterparty()),
        ...routes,
      },
    },
  )
}

describe('CounterpartyPage', () => {
  it('shows the masked card, the findings linking to the queue and the runs', async () => {
    setup()

    expect(await screen.findByText('R.A.P.')).toBeTruthy()
    expect(screen.getByText('****7897')).toBeTruthy()
    expect(screen.getByText('RFC ****1AAA')).toBeTruthy()
    expect(screen.getAllByText(text.status.PENDING_REVIEW).length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'OFAC_SDN' }).getAttribute('href')).toBe('/screening/matches/m7')
    expect(screen.getByRole('table', { name: text.runs.title })).toBeTruthy()
    expect(screen.getByText('r9abcdef')).toBeTruthy()
    expect(document.body.textContent).not.toContain('Rodrigo')
  })

  it('the reveal is an audited mutation that shows the values and the notice, and hide forgets them', async () => {
    const { user, fakeApi, queryClient } = setup()
    await screen.findByText('R.A.P.')
    expect(fakeApi.calls(REVEAL)).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: text.reveal.button }))

    expect(await screen.findByText('Rodrigo Alarcón Palomares')).toBeTruthy()
    expect(screen.getByText('012180001234567897')).toBeTruthy()
    expect(screen.getByText('RFC AAPR800101AAA')).toBeTruthy()
    expect(screen.getByText(text.reveal.notice)).toBeTruthy()
    expect(fakeApi.calls(REVEAL)).toHaveLength(1)
    expect(queryClient.getQueryCache().getAll().map((query) => query.queryKey)).not.toContainEqual(
      expect.arrayContaining(['reveal']),
    )

    await user.click(screen.getByRole('button', { name: text.reveal.hide }))

    expect(document.body.textContent).not.toContain('Rodrigo')
    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll().filter((mutation) => mutation.state.data !== undefined)).toEqual([]),
    )
  })

  it('offers no reveal to an auditor and shows an empty counterparty plainly', async () => {
    setup({ [DETAIL]: () => json(counterpartyDetail({ findings: [], runs: [] })) }, ['AUDITOR_RO'])

    expect(await screen.findByText(text.detail.findingsEmpty)).toBeTruthy()
    expect(screen.getByText(text.runs.empty)).toBeTruthy()
    expect(screen.queryByRole('button', { name: text.reveal.button })).toBeNull()
  })
})
