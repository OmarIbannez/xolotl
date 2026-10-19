// @vitest-environment jsdom
import type { QueryClient } from '@tanstack/react-query'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StaffResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import {
  fakeApi,
  networkFailure,
  pending,
  refuse,
  resetFakeApi,
  respond,
  sentKeys,
  staffUser,
} from '@/test/fake-api'
import { makeAppQueryClient, makeUser, renderWithProviders } from '@/test/render'
import { shownAt, timeCell } from '@/test/time-cells'
import { StaffPage } from './StaffPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const text = esMX.staff
const ME = makeUser() // the signed-in administrator: id 'user-admin'
const MYSELF = staffUser({
  id: ME.id,
  email: ME.email,
  display_name: ME.display_name,
  roles: ['ADMIN'],
  must_change_password: false,
})
const ANA = staffUser({ id: 'staff-2', email: 'ana.perez@acme.mx', display_name: 'Ana Pérez', roles: ['MLRO'] })
const LUIS = staffUser({
  id: 'staff-3',
  email: 'luis.gomez@acme.mx',
  display_name: 'Luis Gómez',
  roles: ['ANALYST', 'SENIOR_ANALYST'],
  status: 'DISABLED',
  must_change_password: false,
})
const CREATED = {
  id: 'staff-9',
  email: 'nora@acme.mx',
  display_name: 'Nora Ruiz',
  roles: ['AUDITOR_RO'],
  initial_password: 'Initial-pass_0123456789',
}

type Page = { items: StaffResponse[]; next_cursor: string | null }

function serve(pages: Record<string, Page>) {
  fakeApi.GET.mockImplementation((path, init) => {
    const page = pages[String(init?.params?.query?.cursor ?? 'first')]
    return path === '/staff' && page ? respond(page) : refuse(404, 'NOT_FOUND')
  })
}

const listCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/staff')
const table = () => screen.getByRole('table', { name: text.table.label })
const rowOf = (email: string) => within(table()).getByRole('row', { name: new RegExp(email) })

/** `queryClient`: the default (no retries), or `makeAppQueryClient()` for the app's own retry policy. */
function setup(queryClient?: QueryClient) {
  const user = userEvent.setup()
  renderWithProviders(<StaffPage />, { user: ME, queryClient })
  return { user }
}

beforeEach(() => {
  resetFakeApi()
  vi.mocked(toast.success).mockClear()
  serve({ first: { items: [MYSELF, ANA, LUIS], next_cursor: null } })
})

describe('StaffPage: states', () => {
  it('shows the table header over placeholders while the first page loads', () => {
    fakeApi.GET.mockImplementation(() => pending())
    setup()

    expect(table().getAttribute('aria-busy')).toBe('true')
    expect(screen.getByRole('columnheader', { name: text.table.email })).toBeTruthy()
  })

  it('says there is nobody yet, still offers to add someone, and shows no pager', async () => {
    serve({ first: { items: [], next_cursor: null } })
    setup()

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByRole('button', { name: text.new })).toBeTruthy()
    expect(screen.queryByRole('navigation', { name: esMX.common.pager.label })).toBeNull()
  })

  it('lists each person with email, name, roles, status, password-change flag and date', async () => {
    setup()

    await screen.findByText('ana.perez@acme.mx')
    expect(
      within(table())
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual([
      text.table.email,
      text.table.displayName,
      text.table.roles,
      text.table.status,
      text.table.mustChangePassword,
      text.table.createdAt,
      text.table.actions,
    ])

    const ana = within(rowOf('ana.perez@acme.mx'))
    expect(ana.getByText('Ana Pérez')).toBeTruthy()
    expect(ana.getByText(esMX.roles.MLRO)).toBeTruthy()
    expect(ana.getByText(text.status.ACTIVE)).toBeTruthy()
    expect(ana.getByText(esMX.common.yes)).toBeTruthy() // still has the temporary password
    expect(timeCell(rowOf('ana.perez@acme.mx'), '2026-10-18T12:00:00Z').textContent).toBe(
      shownAt('2026-10-18T12:00:00Z'),
    )

    const luis = within(rowOf('luis.gomez@acme.mx'))
    expect(luis.getByText(esMX.roles.ANALYST)).toBeTruthy()
    expect(luis.getByText(esMX.roles.SENIOR_ANALYST)).toBeTruthy()
    expect(luis.getByText(text.status.DISABLED)).toBeTruthy()
    expect(luis.getByText(esMX.common.no)).toBeTruthy()
  })

  it('marks the signed-in user, mutes disabled rows, and offers Disable only on other active accounts', async () => {
    setup()
    await screen.findByText('ana.perez@acme.mx')

    expect(within(rowOf('admin@acme.mx')).getByText(esMX.common.you)).toBeTruthy()
    expect(within(rowOf('ana.perez@acme.mx')).queryByText(esMX.common.you)).toBeNull()
    expect(rowOf('luis.gomez@acme.mx').className).toContain('text-muted-foreground')

    expect(within(rowOf('ana.perez@acme.mx')).getByRole('button', { name: 'Deshabilitar a Ana Pérez' })).toBeTruthy()
    expect(within(rowOf('admin@acme.mx')).queryByRole('button')).toBeNull() // no disabling yourself
    expect(within(rowOf('luis.gomez@acme.mx')).queryByRole('button')).toBeNull() // already disabled
  })

  it('shows a failed load as a message with a reference, and retries', async () => {
    fakeApi.GET.mockImplementation(() => refuse(500, 'INTERNAL'))
    const { user } = setup()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.INTERNAL)
    expect(alert.textContent).toContain('req-test')

    serve({ first: { items: [ANA], next_cursor: null } })
    await user.click(within(alert).getByRole('button', { name: esMX.common.retry }))

    expect(await screen.findByText('ana.perez@acme.mx')).toBeTruthy()
  })
})

describe('StaffPage: paging', () => {
  it('asks for the next page with the cursor the API gave, and comes back along it', async () => {
    serve({
      first: { items: [MYSELF], next_cursor: 'cursor-2' },
      'cursor-2': { items: [ANA], next_cursor: null },
    })
    const { user } = setup()
    await screen.findByText('admin@acme.mx')

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByText('ana.perez@acme.mx')).toBeTruthy()
    expect(listCalls().map(([, init]) => init?.params?.query?.cursor)).toEqual([undefined, 'cursor-2'])

    await user.click(screen.getByRole('button', { name: esMX.common.pager.previous }))

    expect(await screen.findByText('admin@acme.mx')).toBeTruthy()
  })
})

describe('StaffPage: a page that fails to load', () => {
  it('shows the failure and still lets the user go back', async () => {
    fakeApi.GET.mockImplementation((_path, init) =>
      init?.params?.query?.cursor === 'cursor-2'
        ? refuse(500, 'INTERNAL')
        : respond({ items: [MYSELF], next_cursor: 'cursor-2' }),
    )
    const { user } = setup()
    await screen.findByText('admin@acme.mx')

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
    await user.click(screen.getByRole('button', { name: esMX.common.pager.previous }))

    expect(await screen.findByText('admin@acme.mx')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('StaffPage: disabling', () => {
  it('asks first, then disables the account and refreshes the list', async () => {
    fakeApi.POST.mockImplementation(() => respond({ ...ANA, status: 'DISABLED' }))
    const { user } = setup()
    await screen.findByText('ana.perez@acme.mx')

    await user.click(screen.getByRole('button', { name: 'Deshabilitar a Ana Pérez' }))
    const dialog = screen.getByRole('dialog', { name: '¿Deshabilitar a Ana Pérez?' })
    expect(fakeApi.POST).not.toHaveBeenCalled()
    expect(dialog.textContent).toContain('ana.perez@acme.mx')
    const listCallsBefore = listCalls().length

    await user.click(within(dialog).getByRole('button', { name: text.disable.confirm }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(fakeApi.POST).toHaveBeenCalledTimes(1)
    const [path, init] = fakeApi.POST.mock.calls[0] ?? []
    expect(path).toBe('/staff/{staff_user_id}/disable')
    expect(init?.params?.path).toEqual({ staff_user_id: 'staff-2' })
    expect(init?.headers?.['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(toast.success).toHaveBeenCalledWith('Se deshabilitó la cuenta de Ana Pérez.')
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(listCallsBefore))
  })

  it('leaves everything alone when the user cancels', async () => {
    const { user } = setup()
    await screen.findByText('ana.perez@acme.mx')

    await user.click(screen.getByRole('button', { name: 'Deshabilitar a Ana Pérez' }))
    await user.click(screen.getByRole('button', { name: esMX.common.cancel }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fakeApi.POST).not.toHaveBeenCalled()
  })

  it('surfaces the 409 in the dialog, in our words, when the API refuses to disable the caller', async () => {
    fakeApi.POST.mockImplementation(() => refuse(409, 'CONFLICT'))
    const { user } = setup()
    await screen.findByText('ana.perez@acme.mx')
    await user.click(screen.getByRole('button', { name: 'Deshabilitar a Ana Pérez' }))

    await user.click(screen.getByRole('button', { name: text.disable.confirm }))

    const dialog = screen.getByRole('dialog')
    const alert = await within(dialog).findByRole('alert')
    expect(alert.textContent).toBe(text.disable.conflict)
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('keeps the dialog open when it fails, and a retry reuses the same key', async () => {
    fakeApi.POST.mockImplementationOnce(() => networkFailure()).mockImplementation(() => respond({ ...ANA, status: 'DISABLED' }))
    const { user } = setup()
    await screen.findByText('ana.perez@acme.mx')
    await user.click(screen.getByRole('button', { name: 'Deshabilitar a Ana Pérez' }))

    await user.click(screen.getByRole('button', { name: text.disable.confirm }))
    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.NETWORK_ERROR)
    await user.click(screen.getByRole('button', { name: text.disable.confirm }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(sentKeys()).toHaveLength(2)
    expect(sentKeys()[1]).toBe(sentKeys()[0])
  })
})

describe('StaffPage: creating', () => {
  it('shows the initial password once, in its own dialog, and drops it when that is closed', async () => {
    fakeApi.POST.mockImplementation(() => respond(CREATED, { status: 201 }))
    const { user } = setup()
    await screen.findByText('ana.perez@acme.mx')
    const listCallsBefore = listCalls().length

    await user.click(screen.getByRole('button', { name: text.new }))
    const form = screen.getByRole('dialog', { name: text.create.title })
    await user.type(within(form).getByLabelText(text.create.email), 'nora@acme.mx')
    await user.type(within(form).getByLabelText(text.create.displayName), 'Nora Ruiz')
    await user.click(within(form).getByRole('checkbox', { name: esMX.roles.AUDITOR_RO }))
    await user.click(within(form).getByRole('button', { name: text.create.submit }))

    const secretDialog = await screen.findByRole('dialog', { name: 'Contraseña inicial de Nora Ruiz' })
    expect(within(secretDialog).getByText(CREATED.initial_password)).toBeTruthy()
    expect(secretDialog.textContent).toContain('nora@acme.mx')
    expect(screen.queryByRole('dialog', { name: text.create.title })).toBeNull()
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(listCallsBefore))

    await user.click(within(secretDialog).getByRole('checkbox', { name: esMX.common.oneTime.acknowledge }))
    await user.click(within(secretDialog).getByRole('button', { name: esMX.common.close }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.body.textContent).not.toContain(CREATED.initial_password) // gone for good
  })
})

describe('StaffPage: what is typed in or comes back from the API is text, never markup', () => {
  const MARKUP = '<img src=x onerror="window.__pwned=1">'
  // The API accepts any address without spaces (its own check is only `x@y.z`), so one like this can exist.
  const MARKUP_EMAIL = '<img/src=x/onerror=window.__pwned=1>@acme.mx'

  /** Nothing in the page turned into an element, and nothing ran. */
  function expectNoMarkupBecameElements() {
    expect(document.body.querySelector('img')).toBeNull()
    expect(Reflect.get(window, '__pwned')).toBeUndefined()
  }

  it('shows a name and an address like markup as that literal text: in the table, on the disable button, in the confirmation', async () => {
    serve({
      first: {
        items: [MYSELF, staffUser({ id: 'staff-x', email: MARKUP_EMAIL, display_name: MARKUP })],
        next_cursor: null,
      },
    })
    const { user } = setup()

    expect(await screen.findByText(MARKUP)).toBeTruthy() // the name cell, as characters
    expect(screen.getByText(MARKUP_EMAIL)).toBeTruthy() // the address cell, as characters
    expectNoMarkupBecameElements()

    await user.click(screen.getByRole('button', { name: `Deshabilitar a ${MARKUP}` }))
    const dialog = screen.getByRole('dialog', { name: `¿Deshabilitar a ${MARKUP}?` })
    expect(dialog.textContent).toContain(MARKUP_EMAIL) // the body names the address
    expectNoMarkupBecameElements()
  })

  it('shows the one-time password dialog as text when the new account has a name and address like markup', async () => {
    fakeApi.POST.mockImplementation(() =>
      respond({ ...CREATED, display_name: MARKUP, email: MARKUP_EMAIL }, { status: 201 }),
    )
    const { user } = setup()
    await screen.findByText('ana.perez@acme.mx')
    await user.click(screen.getByRole('button', { name: text.new }))
    const form = screen.getByRole('dialog', { name: text.create.title })
    await user.type(within(form).getByLabelText(text.create.email), 'nora@acme.mx')
    await user.click(within(form).getByLabelText(text.create.displayName))
    await user.paste(MARKUP)
    await user.click(within(form).getByRole('checkbox', { name: esMX.roles.AUDITOR_RO }))
    await user.click(within(form).getByRole('button', { name: text.create.submit }))

    const secretDialog = await screen.findByRole('dialog', { name: `Contraseña inicial de ${MARKUP}` })
    expect(secretDialog.textContent).toContain(MARKUP_EMAIL) // the hint names the address
    expect(fakeApi.POST.mock.calls[0]?.[1]?.body).toMatchObject({ display_name: MARKUP }) // sent as typed
    expectNoMarkupBecameElements()
  })
})

describe('StaffPage: a 403 from the API (the role does not allow it)', () => {
  it('shows the list refusal in the user language, with its reference, and asks only once', async () => {
    fakeApi.GET.mockImplementation(() => refuse(403, 'ROLE_FORBIDDEN'))
    setup(makeAppQueryClient())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN) // keyed copy
    expect(alert.textContent).toContain('req-test') // the reference to quote
    expect(alert.textContent).not.toContain('English detail') // never the server's text
    expect(screen.queryByRole('table')).toBeNull()
    expect(listCalls()).toHaveLength(1) // the app's retry policy does not ask again after a no
  })

  it('retries a server fault on the list under the same client, so the single ask above means something', async () => {
    fakeApi.GET.mockImplementation(() => refuse(500, 'INTERNAL'))
    setup(makeAppQueryClient())

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
    expect(listCalls()).toHaveLength(3) // the first ask and two retries
  })

  it('shows the refusal of a disable inside its dialog, keeps the dialog open, and sends it once', async () => {
    fakeApi.POST.mockImplementation(() => refuse(403, 'ROLE_FORBIDDEN'))
    const { user } = setup(makeAppQueryClient())
    await screen.findByText('ana.perez@acme.mx')
    await user.click(screen.getByRole('button', { name: 'Deshabilitar a Ana Pérez' }))

    await user.click(screen.getByRole('button', { name: text.disable.confirm }))

    const dialog = screen.getByRole('dialog')
    const alert = await within(dialog).findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN) // not the "you cannot disable yourself" copy
    expect(alert.textContent).not.toContain(text.disable.conflict)
    expect(alert.textContent).toContain('req-test')
    expect(alert.textContent).not.toContain('English detail')
    expect(fakeApi.POST).toHaveBeenCalledTimes(1) // no retry
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})
