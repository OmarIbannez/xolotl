// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { installFakeApi, json, noContent } from '@/test/fake-network'
import { makeQueryClient, providers } from '@/test/render'
import { matchDetail, matchReveal, matchSummary } from './fixtures'
import { useDisposition, useMatch, useMatches, useRevealMatch, useRevokeWhitelist } from './queries'

const MATCHES = 'GET /v1/screening/matches'
const DETAIL = 'GET /v1/screening/matches/m1'
const REVEAL = 'GET /v1/screening/matches/m1/reveal'
const DISPOSE = 'POST /v1/screening/matches/m1/dispositions'
const REVOKE = 'DELETE /v1/screening/whitelist/w1'

function routes() {
  return installFakeApi({
    [MATCHES]: () => json({ items: [matchSummary()], next_cursor: null }),
    [DETAIL]: () => json(matchDetail()),
    [REVEAL]: () => json(matchReveal()),
    [DISPOSE]: () =>
      json(
        { id: 'd1', match_id: 'm1', decision: 'FALSE_POSITIVE', match_status: 'FALSE_POSITIVE', decided_at: '2026-10-20T09:00:00Z' },
        { status: 201 },
      ),
    [REVOKE]: () => noContent(),
  })
}

describe('screening queries', () => {
  it('useMatches sends the page size, the cursor and only the filters set', async () => {
    const api = routes()
    const { Wrapper } = providers()

    const { result } = renderHook(() => useMatches({ band: 'ALERT', status: 'OPEN' }, 'c2'), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(Object.fromEntries(api.queries(MATCHES)[0] ?? [])).toEqual({ limit: '20', cursor: 'c2', band: 'ALERT', status: 'OPEN' })
  })

  it('useMatch reads the masked detail; useRevealMatch calls the reveal only when asked and keeps nothing', async () => {
    const api = routes()
    const queryClient = makeQueryClient()
    const { Wrapper } = providers({ queryClient })

    const { result } = renderHook(() => ({ detail: useMatch('m1'), reveal: useRevealMatch('m1') }), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.detail.isSuccess).toBe(true))
    expect(api.calls(REVEAL)).toHaveLength(0)

    act(() => result.current.reveal.mutate())

    await waitFor(() => expect(result.current.reveal.isSuccess).toBe(true))
    expect(api.calls(REVEAL)).toHaveLength(1)
    const [mutation] = queryClient.getMutationCache().getAll()
    expect(mutation?.options.gcTime).toBe(0)
    expect(queryClient.getQueryCache().getAll()).toHaveLength(1) // the detail only
  })

  it('useDisposition posts with an Idempotency-Key, refreshes the screening queries and is not kept', async () => {
    const api = routes()
    const queryClient = makeQueryClient()
    const { Wrapper } = providers({ queryClient })
    const { result } = renderHook(() => ({ detail: useMatch('m1'), dispose: useDisposition('m1') }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.detail.isSuccess).toBe(true))

    act(() => result.current.dispose.mutate({ decision: 'FALSE_POSITIVE', rationale: 'Diez o más caracteres.' }))

    await waitFor(() => expect(result.current.dispose.isSuccess).toBe(true))
    expect(api.calls(DISPOSE)[0]?.headers.get('Idempotency-Key')).toBeTruthy()
    await waitFor(() => expect(api.calls(DETAIL)).toHaveLength(2))
    expect(queryClient.getMutationCache().getAll()[0]?.options.gcTime).toBe(0)
  })

  it('useRevokeWhitelist sends DELETE and refreshes the screening queries', async () => {
    const api = routes()
    const { Wrapper } = providers()
    const { result } = renderHook(() => ({ detail: useMatch('m1'), revoke: useRevokeWhitelist() }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.detail.isSuccess).toBe(true))

    act(() => result.current.revoke.mutate('w1'))

    await waitFor(() => expect(result.current.revoke.isSuccess).toBe(true))
    expect(api.calls(REVOKE)).toHaveLength(1)
    await waitFor(() => expect(api.calls(DETAIL)).toHaveLength(2))
  })
})
