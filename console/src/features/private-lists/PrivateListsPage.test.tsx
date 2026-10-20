// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PrivateListResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { renderApp } from '@/test/render-app'
import { stubPointerCapture } from '@/test/select'
import { privateList } from './fixtures'
import { PrivateListsPage } from './PrivateListsPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.privateLists
const LISTS = 'GET /v1/private-lists'
const CREATE = 'POST /v1/private-lists'

const INTERNAL = privateList({
  id: 'l2',
  list_code: 'INTERNAL_EX_CLIENTES',
  list_kind: 'INTERNAL',
  title: 'Ex clientes vetados',
  active_version: null,
})

function setup({ items = [privateList(), INTERNAL], routes = {} }: { items?: PrivateListResponse[]; routes?: FakeRoutes } = {}) {
  return renderApp(<PrivateListsPage />, {
    path: '/private-lists',
    routes: { [LISTS]: () => json({ items, next_cursor: null }), ...routes },
  })
}

const table = () => screen.getByRole('table', { name: text.table.label })
async function row(name: RegExp) {
  await waitFor(() => expect(table().getAttribute('aria-busy')).toBeNull())
  return within(within(table()).getByRole('row', { name }))
}

beforeEach(() => {
  stubPointerCapture()
  vi.mocked(toast.success).mockClear()
})

describe('PrivateListsPage: the lists', () => {
  it('shows each list with its kind and active version, or that it has none', async () => {
    setup()

    const lpb = await row(/MX_LPB/)
    expect(lpb.getByText('Lista de Personas Bloqueadas')).toBeTruthy()
    expect(lpb.getByText(text.kind.BLOCKED)).toBeTruthy()
    expect(lpb.getByText('41')).toBeTruthy()
    expect(lpb.getByRole('link', { name: 'MX_LPB' }).getAttribute('href')).toBe('/private-lists/l1')
    const internal = await row(/INTERNAL_EX_CLIENTES/)
    expect(internal.getByText(text.table.noVersion)).toBeTruthy()
    expect(screen.queryByText(text.lpbMissing)).toBeNull()
  })

  it('warns when no LPB list has an active version', async () => {
    setup({ items: [privateList({ active_version: null }), INTERNAL] })

    expect(await screen.findByText(text.lpbMissing)).toBeTruthy()
  })

  it('warns when there is no LPB list at all, and shows the empty state', async () => {
    setup({ items: [] })

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.getByText(text.lpbMissing)).toBeTruthy()
  })

  it('renders titles as text, never as markup', async () => {
    setup({ items: [privateList({ title: '<img src=x onerror=alert(1)>' })] })

    const lpb = await row(/MX_LPB/)
    expect(lpb.getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
  })

  it('shows a failed load', async () => {
    setup({ routes: { [LISTS]: () => problem(500, 'INTERNAL') } })

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.INTERNAL)
  })
})

describe('PrivateListsPage: creating a list', () => {
  it('creates the LPB with an Idempotency-Key and { list_code: "MX_LPB", title }', async () => {
    const { user, fakeApi } = setup({
      items: [],
      routes: { [CREATE]: () => json(privateList({ active_version: null }), { status: 201 }) },
    })

    await user.click(await screen.findByRole('button', { name: text.create.button }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(dialog.getByRole('radio', { name: text.create.typeLpb }))
    await user.type(dialog.getByLabelText(text.create.listTitle), 'LPB de la UIF')
    await user.click(dialog.getByRole('button', { name: text.create.submit }))

    await waitFor(() => expect(fakeApi.calls(CREATE)).toHaveLength(1))
    const [request] = fakeApi.calls(CREATE)
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.json()).toEqual({ list_code: 'MX_LPB', title: 'LPB de la UIF' })
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('creates an internal list as INTERNAL_<SLUG> with its kind', async () => {
    const { user, fakeApi } = setup({
      routes: { [CREATE]: () => json(INTERNAL, { status: 201 }) },
    })

    await user.click(await screen.findByRole('button', { name: text.create.button }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(dialog.getByRole('radio', { name: text.create.typeInternal }))
    await user.type(dialog.getByLabelText(text.create.slug), 'pep_locales')
    await user.click(dialog.getByRole('combobox', { name: text.create.kind }))
    await user.click(await screen.findByRole('option', { name: text.kind.PEP }))
    await user.type(dialog.getByLabelText(text.create.listTitle), 'PEP locales')
    await user.click(dialog.getByRole('button', { name: text.create.submit }))

    await waitFor(() => expect(fakeApi.calls(CREATE)).toHaveLength(1))
    expect(await fakeApi.calls(CREATE)[0]?.json()).toEqual({
      list_code: 'INTERNAL_PEP_LOCALES',
      title: 'PEP locales',
      list_kind: 'PEP',
    })
  })

  it('refuses a bad slug or an empty title before sending anything', async () => {
    const { user, fakeApi } = setup()

    await user.click(await screen.findByRole('button', { name: text.create.button }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(dialog.getByRole('radio', { name: text.create.typeInternal }))
    await user.type(dialog.getByLabelText(text.create.slug), 'ex-clientes')
    await user.click(dialog.getByRole('button', { name: text.create.submit }))

    expect(dialog.getByText(text.create.slugInvalid)).toBeTruthy()
    expect(dialog.getByText(esMX.validation.required)).toBeTruthy()
    expect(fakeApi.calls(CREATE)).toEqual([])
  })

  it('says so when the list already exists (409 PRIVATE_LIST_EXISTS)', async () => {
    const { user } = setup({
      routes: { [CREATE]: () => problem(409, 'PRIVATE_LIST_EXISTS') },
    })

    await user.click(await screen.findByRole('button', { name: text.create.button }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(dialog.getByRole('radio', { name: text.create.typeLpb }))
    await user.type(dialog.getByLabelText(text.create.listTitle), 'Otra LPB')
    await user.click(dialog.getByRole('button', { name: text.create.submit }))

    expect((await dialog.findByRole('alert')).textContent).toContain(esMX.errors.PRIVATE_LIST_EXISTS)
  })
})
