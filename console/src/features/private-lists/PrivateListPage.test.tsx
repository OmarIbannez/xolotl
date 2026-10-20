// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { privateEntry, privateList, privateVersion } from './fixtures'
import { PrivateListPage } from './PrivateListPage'
import { MAX_UPLOAD_BYTES, XLSX_TYPE } from './upload-file'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.privateLists
const UPLOAD = 'POST /v1/private-lists/l1/versions'
const HISTORY = 'GET /v1/private-lists/l1/versions'
const ENTRIES = 'GET /v1/private-lists/l1/entries'
/** A row a user would never want echoed back: it stands for the file's cell values. */
const CSV = 'clave,tipo,nombre_completo,rfc\nLPB-001,PF,JUAN SECRETO PEREZ,NOVALIDO\n'

function setup(routes: FakeRoutes = {}) {
  return renderApp(
    <Routes>
      <Route path="/private-lists/:listId" element={<PrivateListPage />} />
    </Routes>,
    {
      path: '/private-lists/l1',
      me: makeMe({ roles: ['MLRO'] }),
      routes: {
        'GET /v1/private-lists': () => json({ items: [privateList()], next_cursor: null }),
        [HISTORY]: (request) =>
          new URL(request.url).searchParams.get('cursor') === 'c-older'
            ? json({ items: [privateVersion({ id: 'pv1', seq: 1, status: 'SUPERSEDED', entry_count: 40 })], next_cursor: null })
            : json({ items: [privateVersion()], next_cursor: 'c-older' }),
        [UPLOAD]: () => json(privateVersion({ id: 'pv3', seq: 3 }), { status: 201 }),
        [ENTRIES]: (request) =>
          new URL(request.url).searchParams.get('cursor') === 'e-next'
            ? json({ items: [privateEntry({ id: 'pe2', source_entry_key: 'clave LPB-002' })], next_cursor: null })
            : json({ items: [privateEntry()], next_cursor: 'e-next' }),
        ...routes,
      },
    },
  )
}

const fileInput = () => screen.findByLabelText(text.upload.file)
const historyTable = () => screen.getByRole('table', { name: text.history.label })

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('PrivateListPage: uploading a version', () => {
  it('names the list and links the template', async () => {
    setup()

    expect(await screen.findByRole('heading', { name: 'Lista de Personas Bloqueadas' })).toBeTruthy()
    expect(screen.getByRole('link', { name: text.upload.template }).getAttribute('href')).toBe('/plantilla-lista-privada.csv')
  })

  it('sends a CSV as the raw body typed text/csv, with an Idempotency-Key', async () => {
    const { user, fakeApi } = setup()

    await user.upload(await fileInput(), new File([CSV], 'lpb.csv', { type: 'text/csv' }))
    await user.click(screen.getByRole('button', { name: text.upload.submit }))

    await waitFor(() => expect(fakeApi.calls(UPLOAD)).toHaveLength(1))
    const [request] = fakeApi.calls(UPLOAD)
    expect(request?.headers.get('Content-Type')).toBe('text/csv')
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.text()).toBe(CSV)
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('types a spreadsheet by its extension when the browser gives no type', async () => {
    const { user, fakeApi } = setup()

    await user.upload(await fileInput(), new File(['PK'], 'lpb.xlsx', { type: '' }))
    await user.click(screen.getByRole('button', { name: text.upload.submit }))

    await waitFor(() => expect(fakeApi.calls(UPLOAD)).toHaveLength(1))
    expect(fakeApi.calls(UPLOAD)[0]?.headers.get('Content-Type')).toBe(XLSX_TYPE)
  })

  it('shows each row error of a 422 with its translated code, never a value', async () => {
    const { user } = setup({
      [UPLOAD]: () =>
        problem(422, 'PRIVATE_LIST_INVALID', {
          errors: [
            { loc: ['row', 2, 'rfc'], msg: 'invalid RFC', type: 'INVALID_RFC' },
            { loc: ['row', 5, 'tipo'], msg: 'tipo must be PF or PM', type: 'INVALID_TYPE' },
            { loc: ['row', 9, 'clave'], msg: 'the key repeats an earlier row', type: 'DUPLICATE_KEY' },
          ],
        }),
    })

    await user.upload(await fileInput(), new File([CSV], 'lpb.csv', { type: 'text/csv' }))
    await user.click(screen.getByRole('button', { name: text.upload.submit }))

    const errors = await screen.findByRole('table', { name: text.rowErrors.label })
    const rows = within(errors).getAllByRole('row').slice(1)
    expect(rows.map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent))).toEqual([
      ['2', 'rfc', text.rowError.INVALID_RFC],
      ['5', 'tipo', text.rowError.INVALID_TYPE],
      ['9', 'clave', text.rowError.DUPLICATE_KEY],
    ])
    expect(screen.getByText(esMX.errors.PRIVATE_LIST_INVALID)).toBeTruthy()
    const page = document.body.textContent ?? ''
    for (const leaked of ['JUAN SECRETO PEREZ', 'NOVALIDO', 'LPB-001', 'invalid RFC', 'tipo must be']) {
      expect(page).not.toContain(leaked)
    }
  })

  it('shows a whole-file error without a row or column', async () => {
    const { user } = setup({
      [UPLOAD]: () =>
        problem(422, 'PRIVATE_LIST_INVALID', {
          errors: [{ loc: ['file'], msg: 'a required column (clave or tipo) is missing', type: 'MISSING_COLUMN' }],
        }),
    })

    await user.upload(await fileInput(), new File([CSV], 'lpb.csv', { type: 'text/csv' }))
    await user.click(screen.getByRole('button', { name: text.upload.submit }))

    const errors = await screen.findByRole('table', { name: text.rowErrors.label })
    const [row] = within(errors).getAllByRole('row').slice(1)
    expect([...(row?.querySelectorAll('td') ?? [])].map((cell) => cell.textContent)).toEqual([
      text.rowErrors.wholeFile,
      '—',
      text.rowError.MISSING_COLUMN,
    ])
  })

  it('never sends a file over 20 MB, and says why', async () => {
    const { user, fakeApi } = setup()
    const big = new File(['x'], 'big.csv', { type: 'text/csv' })
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 })

    await user.upload(await fileInput(), big)
    await user.click(screen.getByRole('button', { name: text.upload.submit }))

    expect((await screen.findByRole('alert')).textContent).toBe(text.upload.tooLarge)
    expect(fakeApi.calls(UPLOAD)).toEqual([])
  })

  it('never sends a file that is neither CSV nor XLSX', async () => {
    const { user, fakeApi } = setup()

    // applyAccept off: the browser's picker filter is a hint the user can bypass.
    await user.upload(await fileInput(), new File(['%PDF'], 'lpb.pdf', { type: 'application/pdf' }), {
      applyAccept: false,
    })
    await user.click(screen.getByRole('button', { name: text.upload.submit }))

    expect((await screen.findByRole('alert')).textContent).toBe(text.upload.wrongType)
    expect(fakeApi.calls(UPLOAD)).toEqual([])
  })
})

describe('PrivateListPage: history', () => {
  it('lists the versions with their counts and pages to older ones', async () => {
    const { user } = setup()

    await waitFor(() => expect(historyTable().getAttribute('aria-busy')).toBeNull())
    const latest = within(within(historyTable()).getByRole('row', { name: /41/ }))
    expect(latest.getByText(esMX.lists.status.ACTIVE)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))
    expect(await within(historyTable()).findByText(esMX.lists.status.SUPERSEDED)).toBeTruthy()
  })
})

describe('PrivateListPage: revealed entries', () => {
  it('loads no entry until "Revelar", then shows key, type, name and identifiers', async () => {
    const { user, fakeApi } = setup()

    await waitFor(() => expect(historyTable().getAttribute('aria-busy')).toBeNull())
    expect(fakeApi.calls(ENTRIES)).toEqual([])
    expect(screen.queryByText('Persona Ficticia Uno')).toBeNull()

    await user.click(screen.getByRole('button', { name: text.entries.reveal }))

    const entries = await screen.findByRole('table', { name: text.entries.label })
    await within(entries).findByText('Persona Ficticia Uno')
    expect(within(entries).getByText('clave LPB-001')).toBeTruthy()
    expect(within(entries).getByText(esMX.lists.entityType.PERSON)).toBeTruthy()
    expect(within(entries).getByText('RFC XAXX010101000 (MX)')).toBeTruthy()
    expect(fakeApi.calls(ENTRIES)).toHaveLength(1)
  })

  it('asks for each further page with its cursor, one audited view each', async () => {
    const { user, fakeApi } = setup()

    await user.click(await screen.findByRole('button', { name: text.entries.reveal }))
    const entries = await screen.findByRole('table', { name: text.entries.label })
    await within(entries).findByText('clave LPB-001')
    const pagers = screen.getAllByRole('button', { name: esMX.common.pager.next })
    await user.click(pagers[pagers.length - 1] as HTMLElement)

    expect(await within(entries).findByText('clave LPB-002')).toBeTruthy()
    expect(fakeApi.queries(ENTRIES).map((query) => query.get('cursor'))).toEqual([null, 'e-next'])
  })

  it('hides the entries again', async () => {
    const { user } = setup()

    await user.click(await screen.findByRole('button', { name: text.entries.reveal }))
    await screen.findByText('Persona Ficticia Uno')
    await user.click(screen.getByRole('button', { name: text.entries.hide }))

    expect(screen.queryByText('Persona Ficticia Uno')).toBeNull()
  })

  it('renders entry text as text, never as markup', async () => {
    const markup = '<img src=x onerror=alert(1)>'
    const { user } = setup({
      [ENTRIES]: () =>
        json({
          items: [
            privateEntry({
              entry: { ...privateEntry().entry, primary_name: { full: markup, quality: 'PRIMARY' } },
            }),
          ],
          next_cursor: null,
        }),
    })

    await user.click(await screen.findByRole('button', { name: text.entries.reveal }))

    expect(await screen.findByText(markup)).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
  })

  it('shows a refused reveal as an error', async () => {
    const { user } = setup({ [ENTRIES]: () => problem(403, 'ROLE_FORBIDDEN') })

    await user.click(await screen.findByRole('button', { name: text.entries.reveal }))

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.ROLE_FORBIDDEN)
  })
})
