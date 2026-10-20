// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { PartyScreeningStaffResponse, Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { checkResult, neverScreened, partyScreening } from './fixtures'
import { PartyScreeningCard } from './PartyScreeningCard'

const text = esMX.screening
const PARTY = 'GET /v1/screening/parties/p1'
const CHECK = 'POST /v1/screening/checks'

function setup(roles: Role[] = ['ANALYST'], view: PartyScreeningStaffResponse = partyScreening()) {
  return renderApp(<PartyScreeningCard partyId="p1" />, {
    path: '/parties/p1',
    me: makeMe({ roles }),
    routes: {
      [PARTY]: () => json(view),
      [CHECK]: () => json(checkResult(), { status: 201 }),
    },
  })
}

describe('PartyScreeningCard', () => {
  it('shows never screened', async () => {
    setup(['ANALYST'], neverScreened())

    expect(await screen.findByText(text.party.never)).toBeTruthy()
    expect(screen.queryByText(text.party.lastScreened)).toBeNull()
    expect(screen.queryByText(text.party.findings)).toBeNull()
  })

  it('shows the status, the lists covered and the open findings, each linking to its detail', async () => {
    setup()

    expect(await screen.findByText(text.subjectStatus.PENDING_REVIEW)).toBeTruthy()
    expect(screen.getByText('OFAC_SDN, MX_LPB')).toBeTruthy()
    expect(screen.getByText(text.party.openFindings)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'OFAC_SDN' }).getAttribute('href')).toBe('/screening/matches/m1')
    expect(screen.getByText(text.band.ALERT)).toBeTruthy()
    expect(screen.getByText(text.status.OPEN)).toBeTruthy()
  })

  it('check now posts the party id and shows the hits', async () => {
    const { user, fakeApi, queryClient } = setup(['ANALYST'], neverScreened())
    await screen.findByText(text.party.never)

    await user.click(screen.getByRole('button', { name: text.party.checkNow }))

    const result = await screen.findByRole('region', { name: text.party.result })
    const [request] = fakeApi.calls(CHECK)
    expect(await request?.json()).toEqual({ party_id: 'p1' })
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(within(result).getByText(text.subjectStatus.PENDING_REVIEW)).toBeTruthy()
    const table = within(result).getByRole('table', { name: text.hits.label })
    expect(within(table).getByText(text.band.ALERT)).toBeTruthy()
    expect(within(table).getByText(text.band.AUTO_DISCARDED)).toBeTruthy()
    expect(within(table).getByRole('link', { name: text.hits.openFinding }).getAttribute('href')).toBe('/screening/matches/m1')
    expect(within(table).getByText(text.hits.noFinding)).toBeTruthy()
    // The party view is refreshed, and the check's answer is not kept as a query.
    await waitFor(() => expect(fakeApi.calls(PARTY)).toHaveLength(2))
    expect(queryClient.getMutationCache().getAll()[0]?.options.gcTime).toBe(0)
  })

  it('hidden actions for AUDITOR_RO', async () => {
    setup(['AUDITOR_RO'])

    expect(await screen.findByText(text.subjectStatus.PENDING_REVIEW)).toBeTruthy()
    expect(screen.queryByRole('button', { name: text.party.checkNow })).toBeNull()
  })
})
