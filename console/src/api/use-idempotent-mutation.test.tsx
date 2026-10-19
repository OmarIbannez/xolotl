// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { makeQueryClient } from '@/test/render'
import type { Replayable } from './client'
import { ApiError } from './errors'
import { useIdempotentMutation } from './use-idempotent-mutation'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

type Vars = { name: string }
type Result = Replayable<{ id: string }>
type Send = (vars: Vars, key: string) => Promise<Result>

const fresh: Result = { data: { id: 'k-1' }, replayed: false }

function setup(send: Send) {
  const queryClient = makeQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  const hook = renderHook(() => useIdempotentMutation<Vars, Result>({ mutationFn: send }), { wrapper })
  return { ...hook, keys: () => vi.mocked(send).mock.calls.map(([, key]) => key) }
}

const networkFailure = () => new ApiError({ status: 0, code: 'NETWORK_ERROR', detail: 'fetch failed' })

describe('useIdempotentMutation', () => {
  it('hands the call a v4 UUID key and a success carries the call result', async () => {
    const send = vi.fn<Send>(() => Promise.resolve(fresh))
    const { result, keys } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }))

    expect(keys()).toHaveLength(1)
    expect(keys()[0]).toMatch(UUID)
    expect(send.mock.calls[0]?.[0]).toEqual({ name: 'ci' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(fresh)
  })

  it('treats a replayed answer as a success', async () => {
    const replay: Result = { data: { id: 'k-1' }, replayed: true }
    const send = vi.fn<Send>(() => Promise.resolve(replay))
    const { result } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.isError).toBe(false)
    expect(result.current.data?.replayed).toBe(true)
    expect(result.current.data?.data).toEqual({ id: 'k-1' })
  })

  it('retries a submission that failed without an answer with the same key', async () => {
    const send = vi.fn<Send>().mockRejectedValueOnce(networkFailure()).mockResolvedValue(fresh)
    const { result, keys } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }).catch(() => undefined))
    await waitFor(() => expect(result.current.isError).toBe(true))
    await act(() => result.current.mutateAsync({ name: 'ci' }))

    expect(keys()).toHaveLength(2)
    expect(keys()[1]).toBe(keys()[0])
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })

  it('uses a new key for the next submission once one has succeeded', async () => {
    const send = vi.fn<Send>(() => Promise.resolve(fresh))
    const { result, keys } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }))
    await act(() => result.current.mutateAsync({ name: 'ci' }))

    expect(keys()[1]).not.toBe(keys()[0])
  })

  it('uses a new key after the API refuses the submission', async () => {
    const refusal = new ApiError({ status: 409, code: 'CONFLICT', detail: 'exists' })
    const send = vi.fn<Send>().mockRejectedValueOnce(refusal).mockResolvedValue(fresh)
    const { result, keys } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }).catch(() => undefined))
    await act(() => result.current.mutateAsync({ name: 'ci' }))

    expect(keys()[1]).not.toBe(keys()[0])
  })

  it('uses a new key when the values were edited after a failure', async () => {
    const send = vi.fn<Send>().mockRejectedValueOnce(networkFailure()).mockResolvedValue(fresh)
    const { result, keys } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }).catch(() => undefined))
    await act(() => result.current.mutateAsync({ name: 'ci-2' }))

    expect(keys()[1]).not.toBe(keys()[0])
  })

  it('sends a double click once', async () => {
    let finish: (value: Result) => void = () => undefined
    const send = vi.fn<Send>(
      () =>
        new Promise<Result>((resolve) => {
          finish = resolve
        }),
    )
    const { result } = setup(send)

    await act(async () => {
      void result.current.mutateAsync({ name: 'ci' })
      void result.current.mutateAsync({ name: 'ci' })
      await vi.waitFor(() => expect(send).toHaveBeenCalled()) // TanStack calls send a tick later
      finish(fresh)
    })

    expect(send).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })

  it('reset clears the error and forgets the key the failure kept', async () => {
    const send = vi.fn<Send>().mockRejectedValueOnce(networkFailure()).mockResolvedValue(fresh)
    const { result, keys } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }).catch(() => undefined))
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError))
    act(() => result.current.reset())
    await waitFor(() => expect(result.current.error).toBeNull())
    await act(() => result.current.mutateAsync({ name: 'ci' }))

    expect(keys()[1]).not.toBe(keys()[0])
  })

  it('exposes the failure to the caller as the ApiError it was', async () => {
    const send = vi.fn<Send>().mockRejectedValue(networkFailure())
    const { result } = setup(send)

    await act(() => result.current.mutateAsync({ name: 'ci' }).catch(() => undefined))

    await waitFor(() => expect(result.current.error).toMatchObject({ code: 'NETWORK_ERROR', status: 0 }))
  })
})
