// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  apiKey,
  fakeApi,
  refuse,
  resetFakeApi,
  respond,
  sentKeys,
  staffUser,
} from '@/test/fake-api'
import { makeQueryClient, makeUser, providers } from '@/test/render'
import { ApiError } from './errors'
import {
  PAGE_SIZE,
  useApiKeys,
  useCreateApiKey,
  useCreateStaff,
  useDisableStaff,
  useRevokeApiKey,
  useStaff,
} from './queries'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const KEY_PAGE = { items: [apiKey()], next_cursor: 'next-1' }
const STAFF_PAGE = { items: [staffUser()], next_cursor: null }
const CREATED_KEY = { id: 'k2', name: 'ci', key_prefix: 'xk_sbx_a4313e2f', scopes: ['tenants:read'], env: 'SANDBOX', key: 'xk_sbx_secret' }
const CREATED_STAFF = { id: 's2', email: 'a@acme.mx', display_name: 'A', roles: ['ANALYST'], initial_password: 'initial-secret' }

const listCalls = (path: string) => fakeApi.GET.mock.calls.filter(([p]) => p === path)

// One hook per mutation that submits it with fixed values, so a table can drive them alike.
function useSubmitCreateApiKey() {
  const mutation = useCreateApiKey()
  return async () => {
    await mutation.mutateAsync({ name: 'ci', scopes: ['tenants:read'] })
  }
}
function useSubmitRevokeApiKey() {
  const mutation = useRevokeApiKey()
  return async () => {
    await mutation.mutateAsync('key-1')
  }
}
function useSubmitCreateStaff() {
  const mutation = useCreateStaff()
  return async () => {
    await mutation.mutateAsync({ email: 'a@acme.mx', display_name: 'A', roles: ['ANALYST'] })
  }
}
function useSubmitDisableStaff() {
  const mutation = useDisableStaff()
  return async () => {
    await mutation.mutateAsync('staff-1')
  }
}

beforeEach(() => {
  resetFakeApi()
})

describe('the list queries', () => {
  it('useApiKeys asks for one page, with the cursor it was given', async () => {
    fakeApi.GET.mockImplementation(() => respond(KEY_PAGE))
    const { Wrapper } = providers()

    const { result, rerender } = renderHook(({ cursor }: { cursor?: string }) => useApiKeys(cursor), {
      wrapper: Wrapper,
      initialProps: {},
    })
    await waitFor(() => expect(result.current.data).toEqual(KEY_PAGE))
    rerender({ cursor: 'next-1' })
    await waitFor(() => expect(listCalls('/api-keys')).toHaveLength(2))

    expect(listCalls('/api-keys').map(([, init]) => init?.params?.query)).toEqual([
      { limit: PAGE_SIZE, cursor: undefined },
      { limit: PAGE_SIZE, cursor: 'next-1' },
    ])
  })

  it('useStaff asks for one page, with the cursor it was given', async () => {
    fakeApi.GET.mockImplementation(() => respond(STAFF_PAGE))
    const { Wrapper } = providers()

    const { result } = renderHook(() => useStaff('abc'), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.data).toEqual(STAFF_PAGE))
    expect(listCalls('/staff')[0]?.[1]?.params?.query).toEqual({ limit: PAGE_SIZE, cursor: 'abc' })
  })

  it('keeps each tenant and each page under its own cache entry', async () => {
    fakeApi.GET.mockImplementation(() => respond(KEY_PAGE))
    const { Wrapper, queryClient } = providers({ user: makeUser({ tenant_id: 'tenant-7' }) })

    const { result } = renderHook(() => useApiKeys('c1'), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(queryClient.getQueryCache().getAll().map((query) => query.queryKey)).toEqual([['api-keys', 'tenant-7', 'c1']])
  })

  it('does not ask before there is a signed-in user', async () => {
    fakeApi.GET.mockImplementation(() => respond(KEY_PAGE))
    const { Wrapper } = providers({ user: null })

    renderHook(() => useApiKeys(), { wrapper: Wrapper })
    renderHook(() => useStaff(), { wrapper: Wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))

    expect(fakeApi.GET).not.toHaveBeenCalled()
  })

  it('surfaces a failed page as the ApiError it was', async () => {
    fakeApi.GET.mockImplementation(() => refuse(500, 'INTERNAL'))
    const { Wrapper } = providers()

    const { result } = renderHook(() => useApiKeys(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError))
    expect(result.current.error).toMatchObject({ status: 500, code: 'INTERNAL' })
  })
})

describe('the mutations', () => {
  it('useCreateApiKey sends the request under a fresh key, and reports a replay as a success', async () => {
    fakeApi.POST.mockImplementationOnce(() => respond(CREATED_KEY, { status: 201 })).mockImplementationOnce(() =>
      respond(CREATED_KEY, { status: 201, replayed: true }),
    )
    const { Wrapper } = providers()
    const { result } = renderHook(() => useCreateApiKey(), { wrapper: Wrapper })
    const body = { name: 'ci', scopes: ['tenants:read'], env: 'SANDBOX' as const }

    const fresh = await act(() => result.current.mutateAsync(body))
    const replay = await act(() => result.current.mutateAsync({ ...body, name: 'ci again' }))

    expect(fresh).toEqual({ data: CREATED_KEY, replayed: false })
    expect(replay).toEqual({ data: CREATED_KEY, replayed: true })
    expect(fakeApi.POST.mock.calls.map(([path, init]) => [path, init?.body])).toEqual([
      ['/api-keys', body],
      ['/api-keys', { ...body, name: 'ci again' }],
    ])
    expect(sentKeys()[0]).toMatch(UUID)
    expect(sentKeys()[1]).toMatch(UUID)
    expect(sentKeys()[1]).not.toBe(sentKeys()[0])
  })

  it('useCreateStaff sends the request under a fresh key', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED_STAFF, { status: 201 }))
    const { Wrapper } = providers()
    const { result } = renderHook(() => useCreateStaff(), { wrapper: Wrapper })
    const body = { email: 'a@acme.mx', display_name: 'A', roles: ['ANALYST' as const] }

    const created = await act(() => result.current.mutateAsync(body))

    expect(created).toEqual({ data: CREATED_STAFF, replayed: false })
    expect(fakeApi.POST.mock.calls[0]?.[0]).toBe('/staff')
    expect(fakeApi.POST.mock.calls[0]?.[1]?.body).toEqual(body)
    expect(sentKeys()[0]).toMatch(UUID)
  })

  it('useRevokeApiKey and useDisableStaff address the item by id, each under a key', async () => {
    fakeApi.POST.mockImplementation(() => respond({}))
    const { Wrapper } = providers()
    const revoke = renderHook(() => useRevokeApiKey(), { wrapper: Wrapper })
    const disable = renderHook(() => useDisableStaff(), { wrapper: Wrapper })

    await act(() => revoke.result.current.mutateAsync('key-7'))
    await act(() => disable.result.current.mutateAsync('staff-7'))

    expect(fakeApi.POST.mock.calls.map(([path, init]) => [path, init?.params?.path])).toEqual([
      ['/api-keys/{api_key_id}/revoke', { api_key_id: 'key-7' }],
      ['/staff/{staff_user_id}/disable', { staff_user_id: 'staff-7' }],
    ])
    for (const key of sentKeys()) expect(key).toMatch(UUID)
  })

  it.each([
    ['useCreateApiKey', useSubmitCreateApiKey, CREATED_KEY, '/api-keys'],
    ['useRevokeApiKey', useSubmitRevokeApiKey, {}, '/api-keys'],
    ['useCreateStaff', useSubmitCreateStaff, CREATED_STAFF, '/staff'],
    ['useDisableStaff', useSubmitDisableStaff, {}, '/staff'],
  ] as const)('%s refreshes the list it changes, and only that one', async (_name, useSubmit, answer, list) => {
    fakeApi.GET.mockImplementation((path) => respond(path === '/staff' ? STAFF_PAGE : KEY_PAGE))
    fakeApi.POST.mockImplementation(() => respond(answer, { status: 201 }))
    const { Wrapper } = providers()
    const both = renderHook(() => ({ keys: useApiKeys(), staff: useStaff(), submit: useSubmit() }), {
      wrapper: Wrapper,
    })
    await waitFor(() => expect(both.result.current.keys.isSuccess && both.result.current.staff.isSuccess).toBe(true))
    const other = list === '/staff' ? '/api-keys' : '/staff'

    await act(() => both.result.current.submit())

    await waitFor(() => expect(listCalls(list)).toHaveLength(2))
    expect(listCalls(other)).toHaveLength(1)
  })

  it('does not keep what a mutation returned (a one-time secret) in the mutation cache once its caller is gone', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED_KEY, { status: 201 }))
    const queryClient = makeQueryClient()
    const { Wrapper } = providers({ queryClient })
    const { result, unmount } = renderHook(() => useCreateApiKey(), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ name: 'ci', scopes: ['tenants:read'] }))
    unmount()

    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toHaveLength(0))
  })

  it("does not keep a new staff account's initial password in the mutation cache once its caller is gone", async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED_STAFF, { status: 201 }))
    const queryClient = makeQueryClient()
    const { Wrapper } = providers({ queryClient })
    const { result, unmount } = renderHook(() => useCreateStaff(), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ email: 'a@acme.mx', display_name: 'A', roles: ['ANALYST'] }))
    unmount()

    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toHaveLength(0))
  })

  it('surfaces a refusal to the caller as the ApiError it was', async () => {
    fakeApi.POST.mockImplementation(() => refuse(409, 'CONFLICT'))
    const { Wrapper } = providers()
    const { result } = renderHook(() => useCreateStaff(), { wrapper: Wrapper })

    const failure = await act(() =>
      result.current.mutateAsync({ email: 'a@acme.mx', display_name: 'A', roles: ['ANALYST'] }).catch((e: unknown) => e),
    )

    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).toMatchObject({ status: 409, code: 'CONFLICT' })
  })
})
