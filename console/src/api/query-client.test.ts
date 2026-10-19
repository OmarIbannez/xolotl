import { describe, expect, it } from 'vitest'
import { ApiError } from './errors'
import { createQueryClient, shouldRetry } from './query-client'

const answer = (status: number, code: string) => new ApiError({ status, code, detail: `detail of ${code}` })

describe('shouldRetry: what the console asks the API again about', () => {
  it.each([
    ['no answer at all (network)', answer(0, 'NETWORK_ERROR')],
    ['a server fault', answer(500, 'INTERNAL')],
    ['a gateway that cannot reach the API', answer(502, 'HTTP_502')],
    ['an API that is still starting', answer(503, 'HTTP_503')],
    ['a failure that is not an API answer', new TypeError('fetch failed')],
  ])('retries %s, but only twice', (_name, error) => {
    expect(shouldRetry(0, error)).toBe(true)
    expect(shouldRetry(1, error)).toBe(true)
    expect(shouldRetry(2, error)).toBe(false)
  })

  it.each([
    ['400 bad request', answer(400, 'BAD_REQUEST')],
    ['401 no session', answer(401, 'UNAUTHENTICATED')],
    ['403 not allowed', answer(403, 'ROLE_FORBIDDEN')],
    ['403 password change required', answer(403, 'PASSWORD_CHANGE_REQUIRED')],
    ['404 not found', answer(404, 'NOT_FOUND')],
    ['409 conflict', answer(409, 'CONFLICT')],
    ['422 invalid', answer(422, 'VALIDATION_FAILED')],
  ])('never retries an answer that said no: %s', (_name, error) => {
    expect(shouldRetry(0, error)).toBe(false)
    expect(shouldRetry(1, error)).toBe(false)
  })
})

describe('createQueryClient', () => {
  it('is the client the app runs on: that retry policy, and no refetch on window focus', () => {
    const queries = createQueryClient().getDefaultOptions().queries

    expect(queries?.retry).toBe(shouldRetry)
    expect(queries?.refetchOnWindowFocus).toBe(false)
    expect(queries?.staleTime).toBe(15_000)
  })
})
