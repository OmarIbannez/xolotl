// @vitest-environment jsdom
import type { QueryClient } from '@tanstack/react-query'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiKeyResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import {
  apiKey,
  fakeApi,
  networkFailure,
  pending,
  refuse,
  resetFakeApi,
  respond,
  SANDBOX_TENANT,
  sentKeys,
} from '@/test/fake-api'
import { makeAppQueryClient, renderWithProviders } from '@/test/render'
import { shownAt, timeCell } from '@/test/time-cells'
import { ApiKeysPage } from './ApiKeysPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const text = esMX.apiKeys
const ACTIVE = apiKey({ id: 'key-1', name: 'ci-bot', last_used_at: '2026-10-18T15:30:00Z' })
const REVOKED = apiKey({
  id: 'key-2',
  name: 'legacy',
  key_prefix: 'xk_sbx_0badc0de',
  scopes: ['tenants:write'],
  status: 'REVOKED',
  revoked_at: '2026-10-17T10:00:00Z',
})
const CREATED = {
  id: 'key-3',
  name: 'billing',
  key_prefix: 'xk_sbx_c0ffee00',
  scopes: ['tenants:read'],
  env: 'SANDBOX',
  key: 'xk_sbx_c0ffee00_0123456789abcdef0123456789abcdef',
}

type Page = { items: ApiKeyResponse[]; next_cursor: string | null }

/** The API answers `GET /api-keys` per cursor (`first` for none) and `GET /tenants/me`. */
function serve(pages: Record<string, Page>) {
  fakeApi.GET.mockImplementation((path, init) => {
    if (path === '/tenants/me') return respond(SANDBOX_TENANT)
    const page = pages[String(init?.params?.query?.cursor ?? 'first')]
    return path === '/api-keys' && page ? respond(page) : refuse(404, 'NOT_FOUND')
  })
}

const keyListCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/api-keys')
const table = () => screen.getByRole('table', { name: text.table.label })
const rowOf = (name: string) => within(table()).getByRole('row', { name: new RegExp(name) })

/** `queryClient`: the default (no retries), or `makeAppQueryClient()` for the app's own retry policy. */
function setup(queryClient?: QueryClient) {
  const user = userEvent.setup()
  renderWithProviders(<ApiKeysPage />, { queryClient })
  return { user }
}

beforeEach(() => {
  resetFakeApi()
  vi.mocked(toast.success).mockClear()
  serve({ first: { items: [ACTIVE, REVOKED], next_cursor: null } })
})

describe('ApiKeysPage: states', () => {
  it('shows the table header over placeholders while the first page loads', () => {
    fakeApi.GET.mockImplementation((path) => (path === '/api-keys' ? pending() : respond(SANDBOX_TENANT)))
    setup()

    expect(table().getAttribute('aria-busy')).toBe('true')
    expect(screen.getByRole('columnheader', { name: text.table.name })).toBeTruthy()
    expect(screen.queryByText('ci-bot')).toBeNull()
  })

  it('says there are no keys yet, still offers to create one, and shows no pager', async () => {
    serve({ first: { items: [], next_cursor: null } })
    setup()

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByRole('button', { name: text.new })).toBeTruthy()
    expect(screen.queryByRole('navigation', { name: esMX.common.pager.label })).toBeNull()
  })

  it('lists each key with its name, prefix, scopes, environment, status and dates', async () => {
    setup()

    await screen.findByText('ci-bot')
    expect(
      within(table())
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual([
      text.table.name,
      text.table.prefix,
      text.table.scopes,
      text.table.env,
      text.table.status,
      text.table.createdAt,
      text.table.lastUsedAt,
      text.table.actions,
    ])

    const active = within(rowOf('ci-bot'))
    expect(active.getByText('xk_sbx_a4313e2f').tagName).toBe('CODE') // the prefix, in monospace
    expect(active.getByText('tenants:read')).toBeTruthy()
    expect(active.getByText('tenants:write')).toBeTruthy()
    expect(active.getByText(esMX.env.SANDBOX)).toBeTruthy()
    expect(active.getByText(text.status.ACTIVE)).toBeTruthy()
    expect(timeCell(rowOf('ci-bot'), '2026-10-18T12:00:00Z').textContent).toBe(shownAt('2026-10-18T12:00:00Z'))
    expect(timeCell(rowOf('ci-bot'), '2026-10-18T15:30:00Z').textContent).toBe(shownAt('2026-10-18T15:30:00Z'))

    const revoked = within(rowOf('legacy'))
    expect(revoked.getByText(text.status.REVOKED)).toBeTruthy()
    expect(revoked.getByText(esMX.common.never)).toBeTruthy() // never used
  })

  it('mutes revoked rows and offers Revoke only on active keys', async () => {
    setup()
    await screen.findByText('ci-bot')

    expect(rowOf('legacy').className).toContain('text-muted-foreground')
    expect(rowOf('ci-bot').className).not.toContain('text-muted-foreground')
    expect(within(rowOf('ci-bot')).getByRole('button', { name: 'Revocar la llave ci-bot' })).toBeTruthy()
    expect(within(rowOf('legacy')).queryByRole('button')).toBeNull()
  })

  it('shows a failed load as a message with a reference, and retries', async () => {
    fakeApi.GET.mockImplementation((path) => (path === '/api-keys' ? refuse(500, 'INTERNAL') : respond(SANDBOX_TENANT)))
    const { user } = setup()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.INTERNAL)
    expect(alert.textContent).toContain('req-test')
    expect(alert.textContent).not.toContain('English detail')

    serve({ first: { items: [ACTIVE], next_cursor: null } })
    await user.click(within(alert).getByRole('button', { name: esMX.common.retry }))

    expect(await screen.findByText('ci-bot')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('ApiKeysPage: paging', () => {
  it('asks for the next page with the cursor the API gave, and comes back along it', async () => {
    serve({
      first: { items: [ACTIVE], next_cursor: 'cursor-2' },
      'cursor-2': { items: [REVOKED], next_cursor: null },
    })
    const { user } = setup()
    await screen.findByText('ci-bot')
    expect(screen.getByRole('button', { name: esMX.common.pager.previous })).toHaveProperty('disabled', true)

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByText('legacy')).toBeTruthy()
    expect(screen.queryByText('ci-bot')).toBeNull()
    expect(keyListCalls().map(([, init]) => init?.params?.query?.cursor)).toEqual([undefined, 'cursor-2'])
    expect(keyListCalls()[0]?.[1]?.params?.query?.limit).toBe(20)
    expect(screen.getByRole('button', { name: esMX.common.pager.next })).toHaveProperty('disabled', true)

    await user.click(screen.getByRole('button', { name: esMX.common.pager.previous }))

    expect(await screen.findByText('ci-bot')).toBeTruthy()
    expect(screen.getByText('Página 1')).toBeTruthy()
  })
})

describe('ApiKeysPage: a page that fails to load', () => {
  it('shows the failure and still lets the user go back, or retry', async () => {
    fakeApi.GET.mockImplementation((path, init) => {
      if (path === '/tenants/me') return respond(SANDBOX_TENANT)
      return init?.params?.query?.cursor === 'cursor-2'
        ? refuse(500, 'INTERNAL')
        : respond({ items: [ACTIVE], next_cursor: 'cursor-2' })
    })
    const { user } = setup()
    await screen.findByText('ci-bot')

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
    expect(screen.queryByRole('table')).toBeNull()
    const pager = screen.getByRole('navigation', { name: esMX.common.pager.label })
    expect(within(pager).getByRole('button', { name: esMX.common.pager.next })).toHaveProperty('disabled', true)
    await user.click(within(pager).getByRole('button', { name: esMX.common.pager.previous }))

    expect(await screen.findByText('ci-bot')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('ApiKeysPage: revoking', () => {
  it('asks first, then revokes the key and refreshes the list', async () => {
    fakeApi.POST.mockImplementation(() => respond({ ...ACTIVE, status: 'REVOKED' }))
    const { user } = setup()
    await screen.findByText('ci-bot')

    await user.click(screen.getByRole('button', { name: 'Revocar la llave ci-bot' }))
    const dialog = screen.getByRole('dialog', { name: '¿Revocar la llave “ci-bot”?' })
    expect(fakeApi.POST).not.toHaveBeenCalled() // nothing happens before the confirmation
    expect(dialog.textContent).toContain(text.revoke.body)
    const listCallsBefore = keyListCalls().length

    await user.click(within(dialog).getByRole('button', { name: text.revoke.confirm }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(fakeApi.POST).toHaveBeenCalledTimes(1)
    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe('/api-keys/{api_key_id}/revoke')
    expect(init?.params?.path).toEqual({ api_key_id: 'key-1' })
    expect(init?.headers?.['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(toast.success).toHaveBeenCalledWith('Llave “ci-bot” revocada.')
    await waitFor(() => expect(keyListCalls().length).toBeGreaterThan(listCallsBefore)) // list invalidated
  })

  it('leaves everything alone when the user cancels', async () => {
    const { user } = setup()
    await screen.findByText('ci-bot')

    await user.click(screen.getByRole('button', { name: 'Revocar la llave ci-bot' }))
    await user.click(screen.getByRole('button', { name: esMX.common.cancel }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('keeps the dialog open with the reason when it fails, and a retry reuses the same key', async () => {
    fakeApi.POST.mockImplementationOnce(() => networkFailure()).mockImplementation(() => respond({ ...ACTIVE, status: 'REVOKED' }))
    const { user } = setup()
    await screen.findByText('ci-bot')
    await user.click(screen.getByRole('button', { name: 'Revocar la llave ci-bot' }))

    await user.click(screen.getByRole('button', { name: text.revoke.confirm }))

    const dialog = screen.getByRole('dialog')
    expect((await within(dialog).findByRole('alert')).textContent).toContain(esMX.errors.NETWORK_ERROR)
    expect(toast.success).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: text.revoke.confirm }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(sentKeys()).toHaveLength(2)
    expect(sentKeys()[1]).toBe(sentKeys()[0])
  })

  it('cannot be dismissed while the revocation is out', async () => {
    fakeApi.POST.mockImplementation(() => pending())
    const { user } = setup()
    await screen.findByText('ci-bot')
    await user.click(screen.getByRole('button', { name: 'Revocar la llave ci-bot' }))

    await user.click(screen.getByRole('button', { name: text.revoke.confirm }))
    await waitFor(() => expect(fakeApi.POST).toHaveBeenCalled())
    await user.keyboard('{Escape}')

    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByRole('button', { name: esMX.common.cancel })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: text.revoke.confirming })).toHaveProperty('disabled', true)
  })
})

describe('ApiKeysPage: creating', () => {
  it('shows the new key once, in its own dialog, and drops it when that is closed', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user } = setup()
    await screen.findByText('ci-bot')
    const listCallsBefore = keyListCalls().length

    await user.click(screen.getByRole('button', { name: text.new }))
    const form = screen.getByRole('dialog', { name: text.create.title })
    await within(form).findByText(esMX.env.SANDBOX)
    await user.type(within(form).getByLabelText(text.create.name), 'billing')
    await user.click(within(form).getByRole('checkbox', { name: /tenants:read/ }))
    await user.click(within(form).getByRole('button', { name: text.create.submit }))

    const secretDialog = await screen.findByRole('dialog', { name: 'Llave “billing” creada' })
    expect(within(secretDialog).getByText(CREATED.key)).toBeTruthy()
    expect(secretDialog.textContent).toContain(text.secret.hint)
    expect(screen.queryByRole('dialog', { name: text.create.title })).toBeNull() // the form is gone
    await waitFor(() => expect(keyListCalls().length).toBeGreaterThan(listCallsBefore)) // list invalidated

    await user.click(within(secretDialog).getByRole('checkbox', { name: esMX.common.oneTime.acknowledge }))
    await user.click(within(secretDialog).getByRole('button', { name: esMX.common.close }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.queryByText(CREATED.key)).toBeNull() // gone for good
    expect(document.body.textContent).not.toContain(CREATED.key)
  })

  it('goes back to the first page, where the new key is, with the pager starting over', async () => {
    serve({
      first: { items: [ACTIVE], next_cursor: 'cursor-2' },
      'cursor-2': { items: [REVOKED], next_cursor: null },
    })
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user } = setup()
    await screen.findByText('ci-bot')
    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))
    await screen.findByText('legacy')
    expect(screen.getByText('Página 2')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: text.new }))
    const form = screen.getByRole('dialog', { name: text.create.title })
    await within(form).findByText(esMX.env.SANDBOX)
    await user.type(within(form).getByLabelText(text.create.name), 'billing')
    await user.click(within(form).getByRole('checkbox', { name: /tenants:read/ }))
    await user.click(within(form).getByRole('button', { name: text.create.submit }))
    const secretDialog = await screen.findByRole('dialog', { name: 'Llave “billing” creada' })
    await user.click(within(secretDialog).getByRole('checkbox', { name: esMX.common.oneTime.acknowledge }))
    await user.click(within(secretDialog).getByRole('button', { name: esMX.common.close }))

    await waitFor(() => expect(screen.getByText('Página 1')).toBeTruthy())
    expect(await screen.findByText('ci-bot')).toBeTruthy()
    expect(screen.getByRole('button', { name: esMX.common.pager.previous })).toHaveProperty('disabled', true)
    expect(keyListCalls().at(-1)?.[1]?.params?.query?.cursor).toBeUndefined()
  })

  it('starts a fresh form, and a fresh Idempotency-Key, each time it is opened', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user } = setup()
    await screen.findByText('ci-bot')

    async function createOneKey() {
      await user.click(screen.getByRole('button', { name: text.new }))
      const form = screen.getByRole('dialog', { name: text.create.title })
      await within(form).findByText(esMX.env.SANDBOX)
      expect((within(form).getByLabelText(text.create.name) as HTMLInputElement).value).toBe('')
      await user.type(within(form).getByLabelText(text.create.name), 'billing')
      await user.click(within(form).getByRole('checkbox', { name: /tenants:read/ }))
      await user.click(within(form).getByRole('button', { name: text.create.submit }))
      const secretDialog = await screen.findByRole('dialog', { name: 'Llave “billing” creada' })
      await user.click(within(secretDialog).getByRole('checkbox', { name: esMX.common.oneTime.acknowledge }))
      await user.click(within(secretDialog).getByRole('button', { name: esMX.common.close }))
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    }

    await createOneKey()
    await createOneKey() // the same values again: a new submission, so a new key

    expect(sentKeys()).toHaveLength(2)
    expect(sentKeys()[1]).not.toBe(sentKeys()[0])
  })
})

describe('ApiKeysPage: what is typed in or comes back from the API is text, never markup', () => {
  const MARKUP = '<img src=x onerror="window.__pwned=1">'

  /** Nothing in the page turned into an element, and nothing ran. */
  function expectNoMarkupBecameElements() {
    expect(document.body.querySelector('img')).toBeNull()
    expect(Reflect.get(window, '__pwned')).toBeUndefined()
  }

  it('shows a key named like markup as that literal text: in the table, on the revoke button, in the confirmation', async () => {
    serve({ first: { items: [apiKey({ id: 'key-x', name: MARKUP })], next_cursor: null } })
    const { user } = setup()

    expect(await screen.findByText(MARKUP)).toBeTruthy() // the cell's text is the markup, as characters
    expectNoMarkupBecameElements()

    await user.click(screen.getByRole('button', { name: `Revocar la llave ${MARKUP}` }))
    expect(screen.getByRole('dialog', { name: `¿Revocar la llave “${MARKUP}”?` })).toBeTruthy()
    expectNoMarkupBecameElements()
  })

  it('shows the title of the one-time key dialog as text when the name that was typed is markup', async () => {
    fakeApi.POST.mockImplementation(() => respond({ ...CREATED, name: MARKUP }, { status: 201 }))
    const { user } = setup()
    await screen.findByText('ci-bot')
    await user.click(screen.getByRole('button', { name: text.new }))
    const form = screen.getByRole('dialog', { name: text.create.title })
    await within(form).findByText(esMX.env.SANDBOX)
    await user.click(within(form).getByLabelText(text.create.name))
    await user.paste(MARKUP)
    await user.click(within(form).getByRole('checkbox', { name: /tenants:read/ }))
    await user.click(within(form).getByRole('button', { name: text.create.submit }))

    expect(await screen.findByRole('dialog', { name: `Llave “${MARKUP}” creada` })).toBeTruthy()
    expect(fakeApi.POST.mock.calls[0]?.[1]?.body).toMatchObject({ name: MARKUP }) // sent as typed
    expectNoMarkupBecameElements()
  })
})

describe('ApiKeysPage: a 403 from the API (the role does not allow it)', () => {
  it('shows the list refusal in the user language, with its reference, and asks only once', async () => {
    fakeApi.GET.mockImplementation((path) =>
      path === '/api-keys' ? refuse(403, 'ROLE_FORBIDDEN') : respond(SANDBOX_TENANT),
    )
    setup(makeAppQueryClient())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN) // keyed copy
    expect(alert.textContent).toContain('req-test') // the reference to quote
    expect(alert.textContent).not.toContain('English detail') // never the server's text
    expect(screen.queryByRole('table')).toBeNull()
    expect(keyListCalls()).toHaveLength(1) // the app's retry policy does not ask again after a no
  })

  it('retries a server fault on the list under the same client, so the single ask above means something', async () => {
    fakeApi.GET.mockImplementation((path) =>
      path === '/api-keys' ? refuse(500, 'INTERNAL') : respond(SANDBOX_TENANT),
    )
    setup(makeAppQueryClient())

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
    expect(keyListCalls()).toHaveLength(3) // the first ask and two retries
  })

  it('shows the refusal of a revoke inside its dialog, keeps the dialog open, and sends it once', async () => {
    fakeApi.POST.mockImplementation(() => refuse(403, 'ROLE_FORBIDDEN'))
    const { user } = setup(makeAppQueryClient())
    await screen.findByText('ci-bot')
    await user.click(screen.getByRole('button', { name: 'Revocar la llave ci-bot' }))

    await user.click(screen.getByRole('button', { name: text.revoke.confirm }))

    const dialog = screen.getByRole('dialog')
    const alert = await within(dialog).findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN)
    expect(alert.textContent).toContain('req-test')
    expect(alert.textContent).not.toContain('English detail')
    expect(fakeApi.POST).toHaveBeenCalledTimes(1) // no retry
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})
