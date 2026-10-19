import { vi } from 'vitest'
import { ApiError } from '@/api/errors'
import type { ApiKeyResponse, StaffResponse, Tenant } from '@/api/types'

/**
 * A stand-in for the typed client in `@/api/client`, so the real query hooks, mutation hooks
 * and pages run against answers a test chooses. Use it from a test file with
 *
 *     vi.mock('@/api/client', async (importOriginal) => ({
 *       ...(await importOriginal<typeof import('@/api/client')>()),
 *       api: (await import('@/test/fake-api')).fakeApi,
 *     }))
 *
 * The real client throws an `ApiError` for a non-2xx answer; so does a rejection here.
 */

export interface FakeInit {
  body?: unknown
  headers?: Record<string, string>
  params?: { path?: Record<string, string>; query?: Record<string, unknown> }
}

export interface FakeResult {
  data?: unknown
  response: Response
}

type Handler = (path: string, init?: FakeInit) => Promise<FakeResult>

export const fakeApi = { GET: vi.fn<Handler>(), POST: vi.fn<Handler>() }

/** Forget every canned answer and every recorded call. */
export function resetFakeApi(): void {
  fakeApi.GET.mockReset()
  fakeApi.POST.mockReset()
}

/** A successful answer. `replayed` marks one the API served from its idempotency store. */
export function respond(
  data: unknown,
  { status = 200, replayed = false }: { status?: number; replayed?: boolean } = {},
): Promise<FakeResult> {
  const headers: Record<string, string> = replayed ? { 'Idempotent-Replay': 'true' } : {}
  return Promise.resolve({ data, response: new Response(null, { status, headers }) })
}

/** An answer that never arrives, for the loading state and for requests still in flight. */
export function pending(): Promise<FakeResult> {
  return new Promise<FakeResult>(() => undefined)
}

/** A request the API refused (or that never got an answer: status 0). */
export function refuse(
  status: number,
  code: string,
  errors: { loc: (string | number)[]; msg: string; type: string }[] = [],
): Promise<never> {
  return Promise.reject(new ApiError({ status, code, detail: `English detail of ${code}`, errors, requestId: 'req-test' }))
}

export const networkFailure = () => refuse(0, 'NETWORK_ERROR')

/** The `Idempotency-Key` header of every POST sent so far. */
export function sentKeys(): (string | undefined)[] {
  return fakeApi.POST.mock.calls.map(([, init]) => init?.headers?.['Idempotency-Key'])
}

export const SANDBOX_TENANT: Tenant = {
  id: 'tenant-1',
  slug: 'acme',
  legal_name: 'Acme SA de CV',
  country_code: 'MX',
  cell: 'mx-1',
  env: 'SANDBOX',
  status: 'ACTIVE',
  created_at: '2026-10-18T12:00:00Z',
}

export function apiKey(overrides: Partial<ApiKeyResponse> = {}): ApiKeyResponse {
  return {
    id: 'key-1',
    name: 'ci-bot',
    key_prefix: 'xk_sbx_a4313e2f',
    scopes: ['tenants:read', 'tenants:write'],
    env: 'SANDBOX',
    status: 'ACTIVE',
    created_at: '2026-10-18T12:00:00Z',
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  }
}

export function staffUser(overrides: Partial<StaffResponse> = {}): StaffResponse {
  return {
    id: 'staff-2',
    email: 'ana.perez@acme.mx',
    display_name: 'Ana Pérez',
    roles: ['MLRO'],
    status: 'ACTIVE',
    must_change_password: true,
    created_at: '2026-10-18T12:00:00Z',
    ...overrides,
  }
}
