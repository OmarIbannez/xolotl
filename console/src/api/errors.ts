import type { ProblemFieldError } from './types'

/**
 * A failed API call. `code` is the stable problem code (`UNAUTHENTICATED`,
 * `ROLE_FORBIDDEN`, ...), `detail` the server's English explanation (for logs, not for
 * users: map `code` through i18n), `errors` the per-field validation failures.
 * Network failures use status 0 and code `NETWORK_ERROR`; answers that are not a
 * problem document use code `HTTP_<status>`.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string
  readonly errors: readonly ProblemFieldError[]
  /** `X-Request-Id` of the failed call, to quote to support. */
  readonly requestId: string | undefined
  /** Why a refused screening disposition was refused (`DISPOSITION_NOT_ALLOWED`); empty otherwise. */
  readonly blockedReasons: readonly string[]

  constructor(init: {
    status: number
    code: string
    detail: string
    errors?: readonly ProblemFieldError[]
    requestId?: string | undefined
    blockedReasons?: readonly string[]
  }) {
    super(init.detail)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.detail = init.detail
    this.errors = init.errors ?? []
    this.requestId = init.requestId
    this.blockedReasons = init.blockedReasons ?? []
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

/** True when `value` is an `ApiError` carrying this problem code (for example `CONFLICT`). */
export function hasProblemCode(value: unknown, code: string): value is ApiError {
  return isApiError(value) && value.code === code
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function parseStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function parseFieldErrors(value: unknown): ProblemFieldError[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item: unknown) => {
    if (!isRecord(item) || typeof item.msg !== 'string') return []
    const loc = Array.isArray(item.loc)
      ? item.loc.filter((p): p is string | number => typeof p === 'string' || typeof p === 'number')
      : []
    return [{ loc, msg: item.msg, type: typeof item.type === 'string' ? item.type : '' }]
  })
}

/**
 * Read a non-2xx response as the problem+json it should be (spec section 7). A body
 * that is not a problem document (a proxy's HTML page, an empty body, a bare
 * `{"detail": ...}`) becomes a generic `HTTP_<status>` error.
 */
export async function problemFromResponse(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('X-Request-Id') ?? undefined
  let body: unknown
  try {
    body = JSON.parse(await response.clone().text())
  } catch {
    body = undefined
  }
  if (isRecord(body) && typeof body.code === 'string') {
    return new ApiError({
      status: response.status,
      code: body.code,
      detail: typeof body.detail === 'string' ? body.detail : response.statusText,
      errors: parseFieldErrors(body.errors),
      requestId,
      blockedReasons: parseStrings(body.blocked_reasons),
    })
  }
  const detail =
    isRecord(body) && typeof body.detail === 'string' ? body.detail : response.statusText
  return new ApiError({
    status: response.status,
    code: `HTTP_${response.status}`,
    detail: detail || 'request failed',
    requestId,
  })
}
