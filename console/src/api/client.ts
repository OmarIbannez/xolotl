import createClient, { type Middleware } from 'openapi-fetch'
import { session } from '@/auth/session'
import { ApiError, problemFromResponse } from './errors'
import type { paths } from './schema'

/**
 * The OpenAPI document lists routes as `/v1/...`. The client's base URL supplies the
 * prefix, so call sites say `api.GET('/auth/me')`. Unversioned infrastructure routes
 * (`/healthz`, `/readyz`) are not part of the console's API and drop out of the type.
 */
type StripVersion<P> = { [K in keyof P as K extends `/v1${infer Rest}` ? Rest : never]: P[K] }
export type ApiPaths = StripVersion<paths>

const IDEMPOTENCY_HEADER = 'Idempotency-Key'

/** Login steps carry no bearer: they are how a token is obtained in the first place. */
const UNAUTHENTICATED_PATHS: ReadonlySet<string> = new Set(['/auth/login', '/auth/login/totp'])

/**
 * One key per logical mutation attempt. Reuse the same key when retrying that attempt
 * (pass it in the `headers` of the call) so the server replays the first answer
 * instead of repeating the work; use a fresh key for a new attempt.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  // randomUUID needs a secure context (HTTPS or localhost); build a v4 UUID otherwise.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}

function bearerOf(request: Request): string | undefined {
  const header = request.headers.get('Authorization')
  return header?.startsWith('Bearer ') ? header.slice('Bearer '.length) : undefined
}

const conventions: Middleware = {
  onRequest({ request, schemaPath }) {
    const token = session.getToken()
    if (token !== null && !UNAUTHENTICATED_PATHS.has(schemaPath) && !request.headers.has('Authorization')) {
      request.headers.set('Authorization', `Bearer ${token}`)
    }
    // Every POST under /v1 except /v1/auth/* needs an Idempotency-Key.
    if (
      request.method === 'POST' &&
      !schemaPath.startsWith('/auth/') &&
      !request.headers.has(IDEMPOTENCY_HEADER)
    ) {
      request.headers.set(IDEMPOTENCY_HEADER, newIdempotencyKey())
    }
  },

  async onResponse({ request, response }) {
    if (response.ok) return
    const error = await problemFromResponse(response)
    if (response.status === 401) {
      // The guards route to /login when the session is gone.
      session.expire(bearerOf(request))
    } else if (response.status === 403 && error.code === 'PASSWORD_CHANGE_REQUIRED') {
      // The guards route to /setup-password.
      session.requirePasswordChange(bearerOf(request))
    }
    throw error
  },

  onError({ error }) {
    if (error instanceof DOMException && error.name === 'AbortError') return // cancelled on purpose
    return new ApiError({
      status: 0,
      code: 'NETWORK_ERROR',
      detail: error instanceof Error ? error.message : 'network error',
    })
  },
}

/**
 * The typed client for `/v1`. Non-2xx answers reject with an `ApiError`, so a call
 * that resolves has succeeded.
 */
export const api = createClient<ApiPaths>({ baseUrl: '/v1' })
api.use(conventions)

function emptyResponse(response: Response): ApiError {
  return new ApiError({
    status: response.status,
    code: 'EMPTY_RESPONSE',
    detail: 'the response had no body',
    requestId: response.headers.get('X-Request-Id') ?? undefined,
  })
}

/** Await a call and return its JSON body (failures have already thrown an ApiError). */
export async function unwrap<T>(call: Promise<{ data?: T; response: Response }>): Promise<T> {
  const { data, response } = await call
  if (data === undefined) throw emptyResponse(response)
  return data
}

/** Set by the API on an answer it stored for an earlier request with the same `Idempotency-Key`. */
const REPLAY_HEADER = 'Idempotent-Replay'

export interface Replayable<T> {
  data: T
  /**
   * True when the API answered from its idempotency store (`Idempotent-Replay: true`): the
   * work was done by an earlier request with the same key, and this is a copy of its answer.
   * It is still a success, with the same body.
   */
  replayed: boolean
}

/** Like `unwrap`, for mutations: also reports whether the answer was a replay. */
export async function unwrapReplayable<T>(
  call: Promise<{ data?: T; response: Response }>,
): Promise<Replayable<T>> {
  const { data, response } = await call
  if (data === undefined) throw emptyResponse(response)
  return { data, replayed: response.headers.get(REPLAY_HEADER) === 'true' }
}

/** Await a call that answers 204 No Content. */
export async function expectNoContent(call: Promise<{ response: Response }>): Promise<void> {
  await call
}
