import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import type { Me } from '@/api/types'
import { AuthProvider } from '@/auth/AuthProvider'
import { session } from '@/auth/session'
import { installFakeApi, json, type FakeRoutes } from './fake-network'

/** The bearer token of the session `renderApp` starts. */
export const TEST_TOKEN = 'test-token'

/** A signed-in administrator without TOTP, the usual starting point of a test. */
export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: '0191d3a2-6b1c-7000-8000-000000000001',
    tenant_id: '0191d3a2-6b1c-7000-8000-0000000000aa',
    tenant_slug: 'acme',
    email: 'admin@acme.mx',
    display_name: 'Ana Admin',
    roles: ['ADMIN'],
    must_change_password: false,
    totp_enabled: false,
    ...overrides,
  }
}

export interface RenderAppOptions {
  /** The API's routes for this test; `GET /v1/auth/me` answers with `me` unless overridden. */
  routes?: FakeRoutes
  /** The signed-in user's profile. */
  me?: Me
  /** Where the router starts. */
  path?: string
  /** Router state of the starting location (where a guard sent the visitor from, say). */
  state?: unknown
}

/**
 * Render `ui` the way the app mounts a page: a signed-in session (the real `AuthProvider`,
 * loading its profile from the fake API), a fresh query cache that never retries, and a
 * router. Returns a `userEvent` instance and the fake API's request log with it.
 */
export function renderApp(ui: ReactElement, options: RenderAppOptions = {}) {
  const fakeApi = installFakeApi({
    'GET /v1/auth/me': () => json(options.me ?? makeMe()),
    ...options.routes,
  })
  session.start(TEST_TOKEN)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false } },
  })
  const user = userEvent.setup()
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[{ pathname: options.path ?? '/', state: options.state }]}>{ui}</MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { ...rendered, user, fakeApi, queryClient }
}
