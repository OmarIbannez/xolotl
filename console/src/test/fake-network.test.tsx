// @vitest-environment jsdom
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { api, unwrap } from '@/api/client'
import { useAuth } from '@/auth/use-auth'
import { installFakeApi, json, problem } from './fake-network'
import { makeMe, renderApp } from './render-app'

describe('the component-test harness', () => {
  it('lets the real API client run against scripted routes, with its middleware', async () => {
    const fakeApi = installFakeApi({ 'GET /v1/tenants/me': () => json({ slug: 'acme' }) })
    const { session } = await import('@/auth/session')
    session.start('tok-7')

    await unwrap(api.GET('/tenants/me'))

    const [request] = fakeApi.calls('GET /v1/tenants/me')
    expect(request?.url).toBe('http://localhost:3000/v1/tenants/me')
    expect(request?.headers.get('Authorization')).toBe('Bearer tok-7')
  })

  it('keeps request bodies readable and adds the Idempotency-Key to POSTs outside /auth', async () => {
    const fakeApi = installFakeApi({ 'POST /v1/ping': () => json({ pong: 'hi', actor_id: 'a', at: 'now' }) })

    await unwrap(api.POST('/ping', { body: { message: 'hola' } }))

    const [request] = fakeApi.calls('POST /v1/ping')
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.json()).toEqual({ message: 'hola' })
  })

  it('turns a scripted problem into the console ApiError', async () => {
    installFakeApi({ 'GET /v1/audit': () => problem(403, 'ROLE_FORBIDDEN') })

    await expect(api.GET('/audit')).rejects.toMatchObject({
      status: 403,
      code: 'ROLE_FORBIDDEN',
      requestId: 'req-test',
    })
  })

  it('answers a route nobody scripted with 501 and reports it', async () => {
    const fakeApi = installFakeApi({})

    await expect(api.GET('/audit/verify')).rejects.toMatchObject({ status: 501, code: 'UNHANDLED_IN_TEST' })

    expect(fakeApi.unhandled).toEqual(['GET /v1/audit/verify'])
    fakeApi.unhandled.length = 0 // expected here; the setup file fails any other test that leaves one
  })

  it('renders under a signed-in session whose profile comes from the fake API', async () => {
    function WhoAmI() {
      const { user } = useAuth()
      return <p>{user ? `hola ${user.display_name}` : 'cargando'}</p>
    }

    const { fakeApi } = renderApp(<WhoAmI />, { me: makeMe({ display_name: 'Beatriz' }) })

    expect(await screen.findByText('hola Beatriz')).toBeInTheDocument()
    expect(fakeApi.calls('GET /v1/auth/me')).toHaveLength(1)
  })
})
