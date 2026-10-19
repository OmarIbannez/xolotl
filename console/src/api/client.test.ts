import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { session } from '@/auth/session'
import { api, expectNoContent, newIdempotencyKey, unwrap, unwrapReplayable } from './client'
import { ApiError } from './errors'

// openapi-fetch lets each call bring its own fetch and base URL, which is how these
// tests observe the real middleware without a network (a Node Request needs an absolute URL).
const BASE_URL = 'http://api.test/v1'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

type Respond = (request: Request) => Response | Promise<Response>

/** A fetch that records the requests it sees and answers with `respond`. */
function fakeFetch(respond: Respond) {
  const requests: Request[] = []
  const fetch = async (request: Request): Promise<Response> => {
    requests.push(request)
    return respond(request)
  }
  return { fetch, requests }
}

function lastRequest(requests: Request[]): Request {
  const request = requests.at(-1)
  if (!request) throw new Error('no request was sent')
  return request
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

function problem(status: number, code: string, extra: Record<string, unknown> = {}) {
  return new Response(
    JSON.stringify({
      type: `https://xolotl.dev/problems/${code}`,
      title: 'Problem',
      status,
      detail: `detail of ${code}`,
      instance: '/v1/x',
      code,
      ...extra,
    }),
    {
      status,
      headers: { 'content-type': 'application/problem+json', 'x-request-id': 'req-123' },
    },
  )
}

const pong = { pong: 'hi', actor_id: 'a', at: '2026-10-18T00:00:00Z' }
const me = {
  id: 'u1',
  tenant_id: 't1',
  tenant_slug: 'acme',
  email: 'admin@acme.mx',
  display_name: 'Admin',
  roles: ['ADMIN'],
  must_change_password: false,
  totp_enabled: false,
}

beforeEach(() => {
  session.end()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request conventions', () => {
  it('sends the bearer and a fresh Idempotency-Key on a POST outside /auth', async () => {
    session.start('tok-1')
    const { fetch, requests } = fakeFetch(() => json(200, pong))

    await api.POST('/ping', { baseUrl: BASE_URL, fetch, body: { message: 'hi' } })

    const request = lastRequest(requests)
    expect(request.url).toBe(`${BASE_URL}/ping`)
    expect(request.headers.get('Authorization')).toBe('Bearer tok-1')
    expect(request.headers.get('Idempotency-Key')).toMatch(UUID)
  })

  it('generates a new key for each attempt', async () => {
    session.start('tok-1')
    const { fetch, requests } = fakeFetch(() => json(200, pong))

    await api.POST('/ping', { baseUrl: BASE_URL, fetch, body: {} })
    await api.POST('/ping', { baseUrl: BASE_URL, fetch, body: {} })

    const keys = requests.map((request) => request.headers.get('Idempotency-Key'))
    expect(new Set(keys).size).toBe(2)
  })

  it('keeps a key the caller supplies, so retries of one attempt share it', async () => {
    session.start('tok-1')
    const { fetch, requests } = fakeFetch(() => json(200, pong))
    const key = newIdempotencyKey()

    await api.POST('/ping', { baseUrl: BASE_URL, fetch, body: {}, headers: { 'Idempotency-Key': key } })
    await api.POST('/ping', { baseUrl: BASE_URL, fetch, body: {}, headers: { 'Idempotency-Key': key } })

    expect(requests.map((request) => request.headers.get('Idempotency-Key'))).toEqual([key, key])
  })

  it('adds no Idempotency-Key to POSTs under /auth, and no bearer to the login steps', async () => {
    session.start('stale-token')
    const { fetch, requests } = fakeFetch(() => json(200, { access_token: 'a', token_type: 'Bearer', expires_in: 1, must_change_password: false }))

    await api.POST('/auth/login', {
      baseUrl: BASE_URL,
      fetch,
      body: { tenant_slug: 'acme', email: 'a@b.mx', password: 'x' },
    })
    await api.POST('/auth/login/totp', {
      baseUrl: BASE_URL,
      fetch,
      body: { mfa_token: 'm', code: '123456' },
    })

    for (const request of requests) {
      expect(request.headers.has('Idempotency-Key')).toBe(false)
      expect(request.headers.has('Authorization')).toBe(false)
    }
  })

  it('sends the bearer, but no Idempotency-Key, to the authenticated /auth routes', async () => {
    session.start('tok-1')
    const { fetch, requests } = fakeFetch(() => new Response(null, { status: 204 }))

    await api.POST('/auth/password', {
      baseUrl: BASE_URL,
      fetch,
      body: { current_password: 'old', new_password: 'a-new-password-1' },
    })

    const request = lastRequest(requests)
    expect(request.headers.get('Authorization')).toBe('Bearer tok-1')
    expect(request.headers.has('Idempotency-Key')).toBe(false)
  })

  it('adds no Idempotency-Key to GETs', async () => {
    session.start('tok-1')
    const { fetch, requests } = fakeFetch(() => json(200, me))

    await api.GET('/auth/me', { baseUrl: BASE_URL, fetch })

    expect(lastRequest(requests).headers.has('Idempotency-Key')).toBe(false)
  })

  it('omits the bearer when nobody is signed in', async () => {
    const { fetch, requests } = fakeFetch(() => json(200, me))

    await api.GET('/auth/me', { baseUrl: BASE_URL, fetch })

    expect(lastRequest(requests).headers.has('Authorization')).toBe(false)
  })
})

describe('newIdempotencyKey', () => {
  it('returns a v4 UUID', () => {
    expect(newIdempotencyKey()).toMatch(UUID)
  })

  it('still returns a v4 UUID where crypto.randomUUID is unavailable (plain-HTTP origins)', () => {
    vi.stubGlobal('crypto', { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) })

    expect(newIdempotencyKey()).toMatch(UUID)
  })
})

describe('problem+json errors', () => {
  it('turns a problem answer into an ApiError carrying its code, detail and request id', async () => {
    const { fetch } = fakeFetch(() => problem(403, 'ROLE_FORBIDDEN'))

    const error = await api.GET('/tenants/me', { baseUrl: BASE_URL, fetch }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      name: 'ApiError',
      status: 403,
      code: 'ROLE_FORBIDDEN',
      detail: 'detail of ROLE_FORBIDDEN',
      requestId: 'req-123',
      errors: [],
    })
  })

  it('keeps the field errors of a validation failure', async () => {
    const errors = [{ loc: ['body', 'new_password'], msg: 'String should have at least 12 characters', type: 'string_too_short' }]
    const { fetch } = fakeFetch(() => problem(422, 'VALIDATION_FAILED', { errors }))

    const error = await api
      .POST('/auth/password', { baseUrl: BASE_URL, fetch, body: { current_password: 'a', new_password: 'b' } })
      .catch((e: unknown) => e)

    expect(error).toMatchObject({ status: 422, code: 'VALIDATION_FAILED', errors })
  })

  it('falls back to HTTP_<status> when the body is not a problem document', async () => {
    const { fetch } = fakeFetch(
      () => new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway', headers: { 'content-type': 'text/html' } }),
    )

    const error = await api.GET('/auth/me', { baseUrl: BASE_URL, fetch }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 502, code: 'HTTP_502', detail: 'Bad Gateway', errors: [] })
  })

  it('falls back to HTTP_<status> for an empty body and for JSON without a code', async () => {
    const empty = fakeFetch(() => new Response(null, { status: 500 }))
    const plain = fakeFetch(() => json(404, { detail: 'Not Found' }))

    const first = await api.GET('/auth/me', { baseUrl: BASE_URL, fetch: empty.fetch }).catch((e: unknown) => e)
    const second = await api.GET('/auth/me', { baseUrl: BASE_URL, fetch: plain.fetch }).catch((e: unknown) => e)

    expect(first).toMatchObject({ status: 500, code: 'HTTP_500' })
    expect(second).toMatchObject({ status: 404, code: 'HTTP_404', detail: 'Not Found' })
  })

  it('reports a network failure as NETWORK_ERROR with status 0', async () => {
    const { fetch } = fakeFetch(() => {
      throw new TypeError('fetch failed')
    })

    const error = await api.GET('/auth/me', { baseUrl: BASE_URL, fetch }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 0, code: 'NETWORK_ERROR', detail: 'fetch failed' })
  })

  it('lets an aborted request through untouched', async () => {
    const aborted = new DOMException('The operation was aborted.', 'AbortError')
    const { fetch } = fakeFetch(() => {
      throw aborted
    })

    const error = await api.GET('/auth/me', { baseUrl: BASE_URL, fetch }).catch((e: unknown) => e)

    expect(error).toBe(aborted)
  })
})

describe('session effects', () => {
  it('a 401 clears the session', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => problem(401, 'UNAUTHENTICATED'))

    await expect(api.GET('/auth/me', { baseUrl: BASE_URL, fetch })).rejects.toMatchObject({ status: 401 })

    expect(session.getToken()).toBeNull()
    expect(session.getSnapshot().endedBy).toBe('expired')
  })

  it('a late 401 for an old token does not end a newer session', async () => {
    session.start('old-token')
    let answer: (response: Response) => void = () => undefined
    const { fetch, requests } = fakeFetch(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve
        }),
    )

    const pending = api.GET('/auth/me', { baseUrl: BASE_URL, fetch }).catch((e: unknown) => e)
    await vi.waitFor(() => expect(requests).toHaveLength(1))
    session.start('new-token') // the user signed in again while the old request was in flight
    answer(problem(401, 'UNAUTHENTICATED'))
    await pending

    expect(session.getToken()).toBe('new-token')
  })

  it('a failed login, which carries no token, leaves a stored session alone', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => problem(401, 'UNAUTHENTICATED'))

    await expect(
      api.POST('/auth/login', { baseUrl: BASE_URL, fetch, body: { tenant_slug: 'a', email: 'b@c.mx', password: 'x' } }),
    ).rejects.toMatchObject({ status: 401 })

    expect(session.getToken()).toBe('tok-1')
  })

  it('a 403 PASSWORD_CHANGE_REQUIRED flags the session and keeps the token', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => problem(403, 'PASSWORD_CHANGE_REQUIRED'))

    await expect(api.GET('/tenants/me', { baseUrl: BASE_URL, fetch })).rejects.toMatchObject({
      status: 403,
      code: 'PASSWORD_CHANGE_REQUIRED',
    })

    expect(session.getToken()).toBe('tok-1')
    expect(session.getSnapshot().passwordChangeRequired).toBe(true)
  })

  it('a late 403 PASSWORD_CHANGE_REQUIRED for an old token does not gate a newer session', async () => {
    session.start('old-token')
    let answer: (response: Response) => void = () => undefined
    const { fetch, requests } = fakeFetch(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve
        }),
    )

    const pending = api.GET('/tenants/me', { baseUrl: BASE_URL, fetch }).catch((e: unknown) => e)
    await vi.waitFor(() => expect(requests).toHaveLength(1))
    session.start('new-token') // the password was changed while the old request was in flight
    answer(problem(403, 'PASSWORD_CHANGE_REQUIRED'))
    await pending

    expect(session.getToken()).toBe('new-token')
    expect(session.getSnapshot().passwordChangeRequired).toBe(false)
  })

  it('other 403s leave the session alone', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => problem(403, 'ROLE_FORBIDDEN'))

    await expect(api.GET('/tenants/me', { baseUrl: BASE_URL, fetch })).rejects.toMatchObject({ code: 'ROLE_FORBIDDEN' })

    expect(session.getToken()).toBe('tok-1')
    expect(session.getSnapshot().passwordChangeRequired).toBe(false)
  })
})

describe('success helpers', () => {
  it('unwrap returns the JSON body', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => json(200, me))

    await expect(unwrap(api.GET('/auth/me', { baseUrl: BASE_URL, fetch }))).resolves.toEqual(me)
  })

  it('expectNoContent accepts a 204', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => new Response(null, { status: 204 }))

    await expect(
      expectNoContent(
        api.POST('/auth/password', { baseUrl: BASE_URL, fetch, body: { current_password: 'a', new_password: 'b' } }),
      ),
    ).resolves.toBeUndefined()
  })
})

describe('unwrapReplayable', () => {
  const created = {
    id: 'k1',
    name: 'ci',
    key_prefix: 'xk_sbx_1a2b3c4',
    scopes: ['tenants:read'],
    env: 'SANDBOX',
    key: 'xk_sbx_1a2b3c4d_0123456789abcdef0123456789abcdef',
  }
  const createKey = (fetch: (request: Request) => Promise<Response>) =>
    api.POST('/api-keys', {
      baseUrl: BASE_URL,
      fetch,
      body: { name: 'ci', scopes: ['tenants:read'] },
      headers: { 'Idempotency-Key': 'attempt-1' },
    })

  it('returns the body of a fresh answer, not flagged as a replay', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => json(201, created))

    await expect(unwrapReplayable(createKey(fetch))).resolves.toEqual({ data: created, replayed: false })
  })

  it('flags an answer the API replayed from its idempotency store, with the same body: still a success', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(
      () =>
        new Response(JSON.stringify(created), {
          status: 201,
          headers: { 'content-type': 'application/json', 'idempotent-replay': 'true' },
        }),
    )

    await expect(unwrapReplayable(createKey(fetch))).resolves.toEqual({ data: created, replayed: true })
  })

  it('lets the error of a failed call through, as unwrap does', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => problem(409, 'CONFLICT'))

    await expect(unwrapReplayable(createKey(fetch))).rejects.toMatchObject({ status: 409, code: 'CONFLICT' })
  })

  it('rejects with EMPTY_RESPONSE when a success has no body to return', async () => {
    session.start('tok-1')
    const { fetch } = fakeFetch(() => new Response(null, { status: 201 }))

    await expect(unwrapReplayable(createKey(fetch))).rejects.toMatchObject({
      status: 201,
      code: 'EMPTY_RESPONSE',
    })
  })
})
