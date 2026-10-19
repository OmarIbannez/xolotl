import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { vi } from 'vitest'
import { createQueryClient } from '@/api/query-client'
import type { Me } from '@/api/types'
import { hasAnyRole } from '@/auth/access'
import { AuthContext, type AuthValue } from '@/auth/use-auth'

/** A signed-in profile; ADMIN unless a test says otherwise. */
export function makeUser(overrides: Partial<Me> = {}): Me {
  return {
    id: 'user-admin',
    tenant_id: 'tenant-1',
    tenant_slug: 'acme',
    email: 'admin@acme.mx',
    display_name: 'Ada Admin',
    roles: ['ADMIN'],
    must_change_password: false,
    totp_enabled: false,
    ...overrides,
  }
}

/** What `useAuth()` returns for a signed-in `user` (`null` for nobody), with the actions stubbed. */
export function makeAuth(user: Me | null = makeUser()): AuthValue {
  return {
    status: user ? 'authenticated' : 'anonymous',
    user: user ?? undefined,
    error: null,
    endedBy: null,
    mustChangePassword: false,
    login: vi.fn(),
    loginTotp: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshMe: vi.fn(),
    hasRole: (roles) => user !== null && hasAnyRole(user.roles, roles),
  }
}

/** A client for tests: no retries, so a failed call fails at once. */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

/**
 * The client the app itself runs on (`createQueryClient`, so its retry policy too), with no wait
 * between retries so a test that expects retries does not have to sleep.
 */
export function makeAppQueryClient(): QueryClient {
  const client = createQueryClient()
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retryDelay: 0 } })
  return client
}

interface ProviderOptions {
  /** The signed-in user; pass `null` for nobody. */
  user?: Me | null
  queryClient?: QueryClient
}

/**
 * The providers the console's components and hooks read: TanStack Query and the auth context.
 * `Wrapper` is for `renderHook`; `renderWithProviders` uses it for components.
 */
export function providers({ user = makeUser(), queryClient = makeQueryClient() }: ProviderOptions = {}) {
  const auth = makeAuth(user)
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthContext value={auth}>{children}</AuthContext>
      </QueryClientProvider>
    )
  }
  return { Wrapper, queryClient, auth }
}

/** Render a component inside those providers. */
export function renderWithProviders(
  ui: ReactElement,
  { user, queryClient, ...options }: ProviderOptions & Omit<RenderOptions, 'wrapper'> = {},
) {
  const { Wrapper, ...context } = providers({ user, queryClient })
  return { ...context, ...render(ui, { wrapper: Wrapper, ...options }) }
}
