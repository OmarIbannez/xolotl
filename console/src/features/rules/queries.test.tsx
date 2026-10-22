// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { installFakeApi, json, problem } from '@/test/fake-network'
import { providers } from '@/test/render'
import { RULE_ID, rule, simulation, version } from './fixtures'
import { useCreateRule, useRule, useRules, useSimulation, useTransition, useUpdateVersion } from './queries'

const VERSION = `/v1/monitoring/rules/${RULE_ID}/versions/1`

describe('useRules', () => {
  it('asks for a page with the filters as plain pairs, and keeps nothing once unused', async () => {
    const fakeApi = installFakeApi({ 'GET /v1/monitoring/rules': () => json({ items: [rule()], next_cursor: null }) })
    const { Wrapper, queryClient } = providers()

    const { result } = renderHook(() => useRules({ status: 'IN_REVIEW', kind: undefined }, 'c2'), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const [query] = fakeApi.queries('GET /v1/monitoring/rules')
    expect(Object.fromEntries(query ?? [])).toEqual({ status: 'IN_REVIEW', limit: '20', cursor: 'c2' })
    expect(queryClient.getQueryCache().getAll()[0]?.options.gcTime).toBe(0)
  })
})

describe('the writes', () => {
  it('create posts the template code under an Idempotency-Key and reads the rules again', async () => {
    const fakeApi = installFakeApi({
      'POST /v1/monitoring/rules': () => json(version(), { status: 201 }),
      [`GET /v1/monitoring/rules/${RULE_ID}`]: () => json(rule()),
    })
    const { Wrapper } = providers()
    const { result } = renderHook(() => ({ create: useCreateRule(), rule: useRule(RULE_ID) }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.rule.isSuccess).toBe(true))

    await act(async () => {
      await result.current.create.mutateAsync({ template_code: 'CASH_STRUCTURING' })
    })

    const [sent] = fakeApi.calls('POST /v1/monitoring/rules')
    expect(await sent?.json()).toEqual({ template_code: 'CASH_STRUCTURING' })
    expect(sent?.headers.get('Idempotency-Key')).toBeTruthy()
    await waitFor(() => expect(fakeApi.calls(`GET /v1/monitoring/rules/${RULE_ID}`)).toHaveLength(2))
  })

  it('an edit is a PATCH without an Idempotency-Key, a decimal sent as a string', async () => {
    const fakeApi = installFakeApi({ [`PATCH ${VERSION}`]: () => json(version()) })
    const { Wrapper } = providers()
    const { result } = renderHook(() => useUpdateVersion(RULE_ID, 1), { wrapper: Wrapper })

    await act(async () => {
      await result.current.mutateAsync({ params: { near_ratio: '0.75', min_count: 4 } })
    })

    const [sent] = fakeApi.calls(`PATCH ${VERSION}`)
    expect(await sent?.json()).toEqual({ params: { near_ratio: '0.75', min_count: 4 } })
    expect(sent?.headers.get('Idempotency-Key')).toBeNull()
  })

  it('a transition posts to its own route, reject with its reason', async () => {
    const fakeApi = installFakeApi({
      [`POST ${VERSION}/reject`]: () => json(version()),
      [`POST ${VERSION}/approve`]: () => json(version({ status: 'APPROVED' })),
    })
    const { Wrapper } = providers()
    const { result } = renderHook(() => useTransition(RULE_ID, 1), { wrapper: Wrapper })

    await act(async () => {
      await result.current.mutateAsync({ action: 'REJECT', body: { reason: 'Umbral demasiado bajo' } })
    })
    await act(async () => {
      await result.current.mutateAsync({ action: 'APPROVE' })
    })

    const [reject] = fakeApi.calls(`POST ${VERSION}/reject`)
    expect(await reject?.json()).toEqual({ reason: 'Umbral demasiado bajo' })
    expect(reject?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(fakeApi.calls(`POST ${VERSION}/approve`)).toHaveLength(1)
  })
})

describe('a refused transition', () => {
  it('reads the rule again, so the buttons follow the server', async () => {
    const fakeApi = installFakeApi({
      [`POST ${VERSION}/approve`]: () => problem(409, 'RULE_TRANSITION_NOT_ALLOWED', { blocked_reasons: ['SIMULATION_STALE'] }),
      [`GET /v1/monitoring/rules/${RULE_ID}`]: () => json(rule()),
    })
    const { Wrapper } = providers()
    const { result } = renderHook(() => ({ rule: useRule(RULE_ID), transition: useTransition(RULE_ID, 1) }), {
      wrapper: Wrapper,
    })
    await waitFor(() => expect(result.current.rule.isSuccess).toBe(true))

    await act(async () => {
      await result.current.transition.mutateAsync({ action: 'APPROVE' }).catch(() => undefined)
    })

    await waitFor(() => expect(fakeApi.calls(`GET /v1/monitoring/rules/${RULE_ID}`)).toHaveLength(2))
  })
})

describe('useSimulation', () => {
  it('reads a running simulation again until it has finished, then stops', async () => {
    const answers = [simulation({ status: 'QUEUED' }), simulation({ status: 'RUNNING' }), simulation()]
    const fakeApi = installFakeApi({ 'GET /v1/monitoring/simulations/s1': () => json(answers.shift() ?? simulation()) })
    const { Wrapper } = providers()

    const { result } = renderHook(() => useSimulation('s1', { pollMs: 10 }), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.data?.status).toBe('COMPLETED'))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(fakeApi.calls('GET /v1/monitoring/simulations/s1')).toHaveLength(3)
  })

  it.each([
    ['already finished on its first read', [simulation()]],
    ['finished after polling', [simulation({ status: 'RUNNING' }), simulation()]],
    ['failed on its first read', [simulation({ status: 'FAILED', last_error: 'WorkerLost' })]],
  ])('reads the rule again once the simulation has %s', async (_, answers) => {
    const fakeApi = installFakeApi({
      'GET /v1/monitoring/simulations/s1': () => json(answers.length > 1 ? answers.shift() : answers[0]),
      [`GET /v1/monitoring/rules/${RULE_ID}`]: () => json(rule()),
    })
    const { Wrapper } = providers()

    // The page has the rule before the panel reads the simulation it asked for.
    const { result, rerender } = renderHook(
      ({ id }: { id: string | undefined }) => ({ rule: useRule(RULE_ID), simulation: useSimulation(id, { pollMs: 10 }) }),
      { wrapper: Wrapper, initialProps: { id: undefined as string | undefined } },
    )
    await waitFor(() => expect(result.current.rule.isSuccess).toBe(true))
    rerender({ id: 's1' })

    await waitFor(() => expect(result.current.simulation.data?.status).toMatch(/COMPLETED|FAILED/))
    await waitFor(() => expect(fakeApi.calls(`GET /v1/monitoring/rules/${RULE_ID}`)).toHaveLength(2))
  })

  it('asks nothing without a simulation', () => {
    const fakeApi = installFakeApi({})
    const { Wrapper } = providers()

    renderHook(() => useSimulation(undefined), { wrapper: Wrapper })

    expect(fakeApi.requests).toHaveLength(0)
  })
})
