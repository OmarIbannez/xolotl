import { QueryClient } from '@tanstack/react-query'
import { isApiError } from './errors'

/**
 * Whether a failed query is asked again (`failureCount` failures so far). An answer that said no
 * (400, 401, 403, 404, 409, 422) will not change by repeating the question; no answer at all (a
 * network failure, a gateway error) or a server fault (5xx) might pass, so those are retried, twice.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  const mayPass = !isApiError(error) || error.status === 0 || error.status >= 500
  return mayPass && failureCount < 2
}

/** The client the app runs on. Kept here, apart from `main.tsx`, so tests can use the same policy. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: shouldRetry },
    },
  })
}
