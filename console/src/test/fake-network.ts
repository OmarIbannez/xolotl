/**
 * A scriptable stand-in for the API, for tests that render components. The real client
 * (`@/api/client`, middleware included) keeps running: only the network underneath it is
 * replaced, so what a test sees is what the browser would send and receive.
 *
 * Two things are needed for that under jsdom, and both are installed when this module is
 * first loaded (the vitest setup file loads it before any test file):
 *  - `fetch` is an indirection to the routes a test scripts with `installFakeApi`. The API
 *    client reads `globalThis.fetch` once, when its module loads, so it must be stable.
 *  - `Request` accepts relative URLs, resolved against the page like a browser does. The
 *    client's base URL is `/v1`, which Node's own `Request` rejects.
 */

/** Answers one request. */
export type FakeHandler = (request: Request) => Response | Promise<Response>

/** `'METHOD /v1/path'` (the query string is not part of the key) -> its handler. */
export type FakeRoutes = Record<string, FakeHandler>

export interface FakeApi {
  /** Every request the console sent, in order (clones: their bodies can still be read). */
  readonly requests: Request[]
  /** `'METHOD /v1/path'` of requests that no route answered. */
  readonly unhandled: string[]
  /** The requests sent to one route, in order. */
  calls: (route: string) => Request[]
  /** The query strings of the requests sent to one route, in order. */
  queries: (route: string) => URLSearchParams[]
}

let active: { routes: FakeRoutes; api: FakeApi } | null = null

function routeOf(request: Request): string {
  return `${request.method} ${new URL(request.url).pathname}`
}

/** Script the API for the current test; call it before rendering. Cleared after each test. */
export function installFakeApi(routes: FakeRoutes): FakeApi {
  const requests: Request[] = []
  const api: FakeApi = {
    requests,
    unhandled: [],
    calls: (route) => requests.filter((request) => routeOf(request) === route),
    queries: (route) =>
      requests.filter((request) => routeOf(request) === route).map((request) => new URL(request.url).searchParams),
  }
  active = { routes, api }
  return api
}

/** Take the scripted API away, returning the requests it could not answer. */
export function resetFakeApi(): readonly string[] {
  const unhandled = active?.api.unhandled ?? []
  active = null
  return unhandled
}

export function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { 'content-type': 'application/json', ...init.headers },
  })
}

export function noContent(): Response {
  return new Response(null, { status: 204 })
}

/** An `application/problem+json` answer, as the API sends for every non-2xx. */
export function problem(status: number, code: string, extra: Record<string, unknown> = {}): Response {
  return new Response(
    JSON.stringify({
      type: `https://xolotl.dev/problems/${code}`,
      title: code,
      status,
      detail: `English detail of ${code}`,
      instance: '/v1/x',
      code,
      ...extra,
    }),
    { status, headers: { 'content-type': 'application/problem+json', 'x-request-id': 'req-test' } },
  )
}

class PageRelativeRequest extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    super(typeof input === 'string' ? new URL(input, globalThis.location.href).href : input, init)
  }
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request && init === undefined ? input : new Request(input, init)
  if (active === null) throw new TypeError(`fetch without a fake API installed: ${routeOf(request)}`)
  active.api.requests.push(request.clone())
  const handler = active.routes[routeOf(request)]
  if (handler === undefined) {
    active.api.unhandled.push(routeOf(request))
    return problem(501, 'UNHANDLED_IN_TEST')
  }
  return handler(request)
}

if (typeof document !== 'undefined') {
  globalThis.Request = PageRelativeRequest
  globalThis.fetch = fakeFetch
}
