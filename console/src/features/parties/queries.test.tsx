// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PAGE_SIZE } from '@/api/queries'
import { fakeApi, resetFakeApi, respond, sentKeys } from '@/test/fake-api'
import { makeQueryClient, providers } from '@/test/render'
import { party, pii, staffView } from './fixtures'
import { isStaffView, useCreateParty, useParties, useParty, useRevealParty, useSetLegalHold } from './queries'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

beforeEach(() => {
  resetFakeApi()
})

describe('useParties', () => {
  it('asks for a page of PAGE_SIZE (20) with the filters and the cursor', async () => {
    fakeApi.GET.mockImplementation(() => respond({ items: [party()], next_cursor: null }))
    const { Wrapper } = providers()

    const { result } = renderHook(() => useParties({ id_type: 'RFC', value: 'CACX7605101P8' }, 'cursor-2'), {
      wrapper: Wrapper,
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(PAGE_SIZE).toBe(20)
    expect(fakeApi.GET).toHaveBeenCalledTimes(1)
    expect(fakeApi.GET.mock.calls[0]).toEqual([
      '/parties',
      { params: { query: { limit: 20, cursor: 'cursor-2', id_type: 'RFC', value: 'CACX7605101P8' } } },
    ])
  })
})

describe('useParty', () => {
  it('reads the masked detail, which the type guard tells apart from the machine shape', async () => {
    fakeApi.GET.mockImplementation(() => respond(staffView()))
    const { Wrapper } = providers()

    const { result } = renderHook(() => useParty('p1'), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fakeApi.GET.mock.calls[0]).toEqual(['/parties/{party_id}', { params: { path: { party_id: 'p1' } } }])
    expect(result.current.data && isStaffView(result.current.data)).toBe(true)
    expect(isStaffView(party())).toBe(false)
  })
})

describe('useRevealParty', () => {
  it('calls GET /parties/{party_id}/pii only when asked, and the mutation is not kept (gcTime 0)', async () => {
    fakeApi.GET.mockImplementation(() => respond(pii()))
    const queryClient = makeQueryClient()
    const { Wrapper } = providers({ queryClient })

    const { result } = renderHook(() => useRevealParty('p1'), { wrapper: Wrapper })
    expect(fakeApi.GET).not.toHaveBeenCalled()

    act(() => result.current.mutate())

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fakeApi.GET.mock.calls[0]).toEqual(['/parties/{party_id}/pii', { params: { path: { party_id: 'p1' } } }])
    expect(result.current.data?.identifiers[0]?.value).toBe('CACX7605101P8')
    const [mutation] = queryClient.getMutationCache().getAll()
    expect(mutation?.options.gcTime).toBe(0)
    expect(queryClient.getQueryCache().getAll()).toEqual([]) // never a query
  })
})

describe('useCreateParty and useSetLegalHold', () => {
  it('create posts the body under a fresh Idempotency-Key and is not kept', async () => {
    fakeApi.POST.mockImplementation(() => respond(party(), { status: 201 }))
    const queryClient = makeQueryClient()
    const { Wrapper } = providers({ queryClient })
    const { result } = renderHook(() => useCreateParty(), { wrapper: Wrapper })
    const body = { party_type: 'PERSON' as const, person: { given_names: 'Ana', first_surname: 'Pérez' } }

    await act(async () => {
      await result.current.mutateAsync(body)
    })

    expect(fakeApi.POST.mock.calls[0]?.[0]).toBe('/parties')
    expect(fakeApi.POST.mock.calls[0]?.[1]?.body).toEqual(body)
    expect(sentKeys()[0]).toMatch(UUID)
    expect(queryClient.getMutationCache().getAll()[0]?.options.gcTime).toBe(0)
  })

  it('legal hold posts { legal_hold } to the party under an Idempotency-Key', async () => {
    fakeApi.POST.mockImplementation(() => respond(party({ legal_hold: true })))
    const { Wrapper } = providers()
    const { result } = renderHook(() => useSetLegalHold('p1'), { wrapper: Wrapper })

    await act(async () => {
      await result.current.mutateAsync({ legal_hold: true })
    })

    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe('/parties/{party_id}/legal-hold')
    expect(init?.params?.path).toEqual({ party_id: 'p1' })
    expect(init?.body).toEqual({ legal_hold: true })
    expect(sentKeys()[0]).toMatch(UUID)
  })
})
