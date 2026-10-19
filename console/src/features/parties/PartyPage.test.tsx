// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { party, pii, RFC, staffView } from './fixtures'
import { PartyPage } from './PartyPage'

const text = esMX.parties

function setup(roles: Role[] = ['ANALYST'], extra: Parameters<typeof renderApp>[1] = {}) {
  return renderApp(
    <Routes>
      <Route path="/parties/:partyId" element={<PartyPage />} />
    </Routes>,
    {
      path: '/parties/p1',
      me: makeMe({ roles }),
      routes: {
        'GET /v1/parties/p1': () => json(staffView()),
        'GET /v1/parties/p1/pii': () => json(pii()),
        ...extra.routes,
      },
    },
  )
}

describe('PartyPage: the masked summary and the explicit reveal', () => {
  it('shows the masked identifier, reveals the plaintext only on request, and drops it on hide', async () => {
    const { user, fakeApi, queryClient } = setup()

    expect(await screen.findByRole('heading', { name: text.detail.title, level: 1 })).toBeTruthy()
    expect(await screen.findByText('****01P8')).toBeTruthy()
    expect(document.body.textContent).not.toContain(RFC)
    expect(fakeApi.calls('GET /v1/parties/p1/pii')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: text.reveal.button }))

    expect(await screen.findByText(RFC)).toBeTruthy()
    expect(screen.getByText(text.reveal.notice)).toBeTruthy()
    expect(screen.getByText('Ana María')).toBeTruthy()
    expect(screen.getByText('Pérez Gómez')).toBeTruthy()
    expect(screen.getByText('ana@acme.mx')).toBeTruthy()
    expect(fakeApi.calls('GET /v1/parties/p1/pii')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: text.reveal.hide }))

    await waitFor(() => expect(document.body.textContent).not.toContain(RFC))
    expect(document.body.textContent).not.toContain('Ana María')
    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toEqual([]))
    // Nothing the reveal returned is in the query cache either.
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(RFC)
  })

  it('shows the retention date and the legal hold of the summary', async () => {
    setup(['ANALYST'], {
      routes: { 'GET /v1/parties/p1': () => json(staffView({ legal_hold: true, retention_until: '2037-03-01' })) },
    })

    expect(await screen.findByText('1 de marzo de 2037')).toBeTruthy()
    expect(screen.getAllByText(text.detail.legalHoldOn).length).toBeGreaterThan(0)
  })

  it('shows a party the API does not know as an error', async () => {
    renderApp(
      <Routes>
        <Route path="/parties/:partyId" element={<PartyPage />} />
      </Routes>,
      {
        path: '/parties/p1',
        routes: {
          'GET /v1/parties/p1': () =>
            new Response(JSON.stringify({ code: 'NOT_FOUND', detail: 'x' }), {
              status: 404,
              headers: { 'content-type': 'application/problem+json' },
            }),
        },
      },
    )

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.NOT_FOUND)
  })
})

describe('PartyPage: the legal hold', () => {
  it('is offered to MLRO, and posts { legal_hold: true } after the confirm', async () => {
    const { user, fakeApi } = setup(['MLRO'], {
      routes: { 'POST /v1/parties/p1/legal-hold': () => json(party({ legal_hold: true })) },
    })

    await user.click(await screen.findByRole('button', { name: text.legalHold.set }))
    const dialog = screen.getByRole('dialog', { name: text.legalHold.set })
    expect(dialog.textContent).toContain(text.legalHold.confirmSet)
    expect(fakeApi.calls('POST /v1/parties/p1/legal-hold')).toHaveLength(0)

    await user.click(within(dialog).getByRole('button', { name: text.legalHold.set }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const [request] = fakeApi.calls('POST /v1/parties/p1/legal-hold')
    expect(await request?.json()).toEqual({ legal_hold: true })
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('offers MLRO to release a hold that is on', async () => {
    setup(['MLRO'], { routes: { 'GET /v1/parties/p1': () => json(staffView({ legal_hold: true })) } })

    expect(await screen.findByRole('button', { name: text.legalHold.clear })).toBeTruthy()
  })

  it.each(['AUDITOR_RO', 'ADMIN', 'ANALYST'] as const)('is not shown to %s, who still sees the page and the reveal', async (role) => {
    setup([role])

    expect(await screen.findByText('****01P8')).toBeTruthy()
    expect(screen.getByRole('button', { name: text.reveal.button })).toBeTruthy()
    expect(screen.queryByRole('button', { name: text.legalHold.clear })).toBeNull()
    expect(screen.queryByRole('button', { name: text.legalHold.set })).toBeNull()
  })
})
