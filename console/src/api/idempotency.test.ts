import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import { createIdempotentRunner, outcomeIsUnknown } from './idempotency'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

const apiError = (status: number, code: string) => new ApiError({ status, code, detail: `detail of ${code}` })

/** Keys k1, k2, ... so a test can tell exactly which one each request carried. */
function numberedKeys() {
  let n = 0
  return () => `k${++n}`
}

type Send = (vars: { name: string }, key: string) => Promise<string>

function setup() {
  const runner = createIdempotentRunner<{ name: string }, string>(numberedKeys())
  const send = vi.fn<Send>(() => Promise.resolve('ok'))
  return { runner, send, keysSent: () => send.mock.calls.map(([, key]) => key) }
}

/** A request that stays in flight until the test settles it. */
function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (reason: unknown) => void = () => undefined
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('outcomeIsUnknown', () => {
  it('is true when the request may have taken effect: no answer, a server fault, a request still running', () => {
    expect(outcomeIsUnknown(apiError(0, 'NETWORK_ERROR'))).toBe(true)
    expect(outcomeIsUnknown(apiError(500, 'INTERNAL'))).toBe(true)
    expect(outcomeIsUnknown(apiError(502, 'HTTP_502'))).toBe(true)
    expect(outcomeIsUnknown(apiError(503, 'HTTP_503'))).toBe(true)
    expect(outcomeIsUnknown(apiError(409, 'IDEMPOTENCY_IN_PROGRESS'))).toBe(true)
    expect(outcomeIsUnknown(apiError(201, 'EMPTY_RESPONSE'))).toBe(true) // a 2xx we could not read
  })

  it('is true for a failure that is not an API answer at all', () => {
    expect(outcomeIsUnknown(new TypeError('fetch failed'))).toBe(true)
    expect(outcomeIsUnknown('boom')).toBe(true)
  })

  it('is false when the API refused the request, which then did nothing', () => {
    expect(outcomeIsUnknown(apiError(409, 'CONFLICT'))).toBe(false)
    expect(outcomeIsUnknown(apiError(422, 'VALIDATION_FAILED'))).toBe(false)
    expect(outcomeIsUnknown(apiError(422, 'IDEMPOTENCY_KEY_REUSED'))).toBe(false)
    expect(outcomeIsUnknown(apiError(403, 'ROLE_FORBIDDEN'))).toBe(false)
    expect(outcomeIsUnknown(apiError(404, 'NOT_FOUND'))).toBe(false)
    expect(outcomeIsUnknown(apiError(401, 'UNAUTHENTICATED'))).toBe(false)
  })
})

describe('createIdempotentRunner', () => {
  it('sends a fresh v4 UUID as the key when none is injected', async () => {
    const runner = createIdempotentRunner<string, string>()
    const send = vi.fn((_: string, key: string) => Promise.resolve(key))

    await expect(runner.run('x', send)).resolves.toMatch(UUID)
  })

  it('gives every new submission its own key', async () => {
    const { runner, send, keysSent } = setup()

    await runner.run({ name: 'a' }, send)
    await runner.run({ name: 'a' }, send) // the same values, submitted again after a success
    await runner.run({ name: 'b' }, send)

    expect(keysSent()).toEqual(['k1', 'k2', 'k3'])
  })

  it.each([
    ['a network failure', apiError(0, 'NETWORK_ERROR')],
    ['a 500', apiError(500, 'INTERNAL')],
    ['a 502 from a proxy', apiError(502, 'HTTP_502')],
    ['a request still in progress', apiError(409, 'IDEMPOTENCY_IN_PROGRESS')],
    ['a failure that is not an ApiError', new TypeError('fetch failed')],
  ])('reuses the key to retry the same submission after %s', async (_name, failure) => {
    const { runner, send, keysSent } = setup()
    send.mockRejectedValueOnce(failure)

    await expect(runner.run({ name: 'a' }, send)).rejects.toBe(failure)
    await runner.run({ name: 'a' }, send) // the user presses submit again

    expect(keysSent()).toEqual(['k1', 'k1'])
  })

  it('keeps retrying with the same key for as long as the outcome stays unknown', async () => {
    const { runner, send, keysSent } = setup()
    send.mockRejectedValueOnce(apiError(0, 'NETWORK_ERROR')).mockRejectedValueOnce(apiError(503, 'HTTP_503'))

    await runner.run({ name: 'a' }, send).catch(() => undefined)
    await runner.run({ name: 'a' }, send).catch(() => undefined)
    await runner.run({ name: 'a' }, send)

    expect(keysSent()).toEqual(['k1', 'k1', 'k1'])
  })

  it('starts a new submission, with a new key, after the API refuses the request', async () => {
    const { runner, send, keysSent } = setup()
    send.mockRejectedValueOnce(apiError(409, 'CONFLICT'))

    await runner.run({ name: 'a' }, send).catch(() => undefined)
    await runner.run({ name: 'a' }, send)

    expect(keysSent()).toEqual(['k1', 'k2'])
  })

  it('starts a new submission after a success, so the next key is fresh', async () => {
    const { runner, send, keysSent } = setup()

    await runner.run({ name: 'a' }, send)
    send.mockRejectedValueOnce(apiError(0, 'NETWORK_ERROR'))
    await runner.run({ name: 'a' }, send).catch(() => undefined)
    await runner.run({ name: 'a' }, send)

    expect(keysSent()).toEqual(['k1', 'k2', 'k2'])
  })

  it('treats edited values as a new submission even after an unknown failure', async () => {
    // The API compares the request body for a key, so the same key with other values would be refused.
    const { runner, send, keysSent } = setup()
    send.mockRejectedValueOnce(apiError(0, 'NETWORK_ERROR'))

    await runner.run({ name: 'a' }, send).catch(() => undefined)
    await runner.run({ name: 'b' }, send)

    expect(keysSent()).toEqual(['k1', 'k2'])
  })

  it('joins a double submit instead of sending the same request twice', async () => {
    const { runner, send } = setup()
    const pending = deferred<string>()
    send.mockReturnValueOnce(pending.promise)

    const first = runner.run({ name: 'a' }, send)
    const second = runner.run({ name: 'a' }, send) // a second click while the first is in flight
    pending.resolve('created')

    await expect(first).resolves.toBe('created')
    await expect(second).resolves.toBe('created')
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('joins a double submit that fails, and the retry afterwards still uses the same key', async () => {
    const { runner, send, keysSent } = setup()
    const pending = deferred<string>()
    send.mockReturnValueOnce(pending.promise)

    const first = runner.run({ name: 'a' }, send).catch((e: unknown) => e)
    const second = runner.run({ name: 'a' }, send).catch((e: unknown) => e)
    const failure = apiError(0, 'NETWORK_ERROR')
    pending.reject(failure)

    expect(await first).toBe(failure)
    expect(await second).toBe(failure)
    await runner.run({ name: 'a' }, send)
    expect(keysSent()).toEqual(['k1', 'k1'])
  })

  it('does not join a submission with different values: it is its own request with its own key', async () => {
    const { runner, send, keysSent } = setup()
    const pending = deferred<string>()
    send.mockReturnValueOnce(pending.promise)

    const first = runner.run({ name: 'a' }, send)
    await runner.run({ name: 'b' }, send)
    pending.resolve('created')
    await first

    expect(keysSent()).toEqual(['k1', 'k2'])
  })

  it('lets a new submission through once the previous one has settled', async () => {
    const { runner, send } = setup()

    await runner.run({ name: 'a' }, send)
    await runner.run({ name: 'a' }, send)

    expect(send).toHaveBeenCalledTimes(2)
  })

  it('passes the variables through untouched and resolves with what send resolves', async () => {
    const { runner, send } = setup()
    send.mockResolvedValueOnce('created')

    await expect(runner.run({ name: 'a' }, send)).resolves.toBe('created')
    expect(send.mock.calls[0]?.[0]).toEqual({ name: 'a' })
  })

  it('treats a replayed answer as the success it is: the key is spent and the next submission is new', async () => {
    const runner = createIdempotentRunner<{ name: string }, { replayed: boolean }>(numberedKeys())
    const keys: string[] = []
    const send = vi.fn((_: { name: string }, key: string) => {
      keys.push(key)
      return Promise.resolve({ replayed: true })
    })

    await expect(runner.run({ name: 'a' }, send)).resolves.toEqual({ replayed: true })
    await runner.run({ name: 'a' }, send)

    expect(keys).toEqual(['k1', 'k2'])
  })

  it('reset forgets the key kept after an unknown failure', async () => {
    const { runner, send, keysSent } = setup()
    send.mockRejectedValueOnce(apiError(0, 'NETWORK_ERROR'))

    await runner.run({ name: 'a' }, send).catch(() => undefined)
    runner.reset()
    await runner.run({ name: 'a' }, send)

    expect(keysSent()).toEqual(['k1', 'k2'])
  })

  it('reset does not disturb a request in flight: a repeat of it still joins', async () => {
    const { runner, send } = setup()
    const pending = deferred<string>()
    send.mockReturnValueOnce(pending.promise)

    const first = runner.run({ name: 'a' }, send)
    runner.reset()
    const second = runner.run({ name: 'a' }, send)
    pending.resolve('created')
    await Promise.all([first, second])

    expect(send).toHaveBeenCalledTimes(1)
  })
})
