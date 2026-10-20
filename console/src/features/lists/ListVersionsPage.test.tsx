// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { deltaRow, listEntry, version } from './fixtures'
import { ListVersionsPage } from './ListVersionsPage'

const text = esMX.lists
const BASE = '/v1/lists/UN_CONSOLIDATED'

const DELTA = [
  deltaRow(),
  deltaRow({
    source_entry_key: 'QDi.002',
    change: 'CHANGED',
    prev_entry_id: 'e-old-2',
    new_entry_id: 'e-new-2',
    prev_name: 'Nombre Viejo',
    new_name: 'Nombre Nuevo',
  }),
  deltaRow({
    source_entry_key: 'QDi.003',
    change: 'REMOVED',
    prev_entry_id: 'e-old-3',
    new_entry_id: null,
    prev_name: 'Nombre Quitado',
    new_name: null,
  }),
]

function setup(routes: FakeRoutes = {}) {
  return renderApp(
    <Routes>
      <Route path="/lists/:code" element={<ListVersionsPage />} />
    </Routes>,
    {
      path: '/lists/UN_CONSOLIDATED',
      me: makeMe({ roles: ['ANALYST'] }),
      routes: {
        [`GET ${BASE}/versions`]: (request) =>
          new URL(request.url).searchParams.get('cursor') === 'c-older'
            ? json({ items: [version({ id: 'v1', seq: 1, status: 'SUPERSEDED', added: 1002, changed: 0, removed: 0 })], next_cursor: null })
            : json({ items: [version()], next_cursor: 'c-older' }),
        [`GET ${BASE}/versions/v2/delta`]: (request) =>
          new URL(request.url).searchParams.get('cursor') === 'QDi.003'
            ? json({ items: [deltaRow({ source_entry_key: 'QDi.004', new_name: 'Cuarto Nombre' })], next_cursor: null })
            : json({ items: DELTA, next_cursor: 'QDi.003' }),
        [`GET ${BASE}/entries/e-new-2`]: () => json(listEntry()),
        ...routes,
      },
    },
  )
}

const versionsTable = () => screen.getByRole('table', { name: text.versions.label })
const deltaTable = () => screen.getByRole('table', { name: text.delta.label })
async function loaded(table: () => HTMLElement) {
  await waitFor(() => expect(table().getAttribute('aria-busy')).toBeNull())
  return within(table())
}

describe('ListVersionsPage: the versions', () => {
  it('lists the versions newest first, with their counts, and pages back by cursor', async () => {
    const { user, fakeApi } = setup()

    expect(await screen.findByRole('heading', { level: 1, name: /UN_CONSOLIDATED/ })).toBeTruthy()
    const versions = await loaded(versionsTable)
    const latest = within(versions.getByRole('row', { name: /1,003/ }))
    expect(latest.getByText(text.status.ACTIVE)).toBeTruthy()
    expect(latest.getByText('poll')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: esMX.common.pager.next }))

    expect(await screen.findByText(text.status.SUPERSEDED)).toBeTruthy()
    expect(fakeApi.queries(`GET ${BASE}/versions`).map((query) => query.get('cursor'))).toEqual([null, 'c-older'])
  })

  it('shows a failed load as an error', async () => {
    setup({ [`GET ${BASE}/versions`]: () => problem(404, 'NOT_FOUND') })

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.NOT_FOUND)
  })
})

describe('ListVersionsPage: the delta of a version', () => {
  it('asks for a delta only once a version is selected', async () => {
    const { user, fakeApi } = setup()

    await loaded(versionsTable)
    expect(fakeApi.calls(`GET ${BASE}/versions/v2/delta`)).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: text.versions.show.replace('{{seq}}', '2') }))

    const delta = await loaded(deltaTable)
    expect(delta.getByText('Nuevo Nombre')).toBeTruthy()
    expect(delta.getByText('Nombre Quitado')).toBeTruthy()
    expect(fakeApi.calls(`GET ${BASE}/versions/v2/delta`)).toHaveLength(1)
  })

  it('filters the page to the changed entries, with the previous and the new name side by side', async () => {
    const { user } = setup()

    await loaded(versionsTable)
    await user.click(screen.getByRole('button', { name: text.versions.show.replace('{{seq}}', '2') }))
    await loaded(deltaTable)
    await user.click(screen.getByRole('button', { name: text.change.CHANGED }))

    const delta = within(deltaTable())
    const changed = within(delta.getByRole('row', { name: /QDi\.002/ }))
    expect(changed.getByText('Nombre Viejo')).toBeTruthy()
    expect(changed.getByText('Nombre Nuevo')).toBeTruthy()
    expect(delta.queryByText('Nuevo Nombre')).toBeNull()
    expect(delta.queryByText('Nombre Quitado')).toBeNull()
    expect(screen.getByRole('button', { name: text.change.CHANGED }).getAttribute('aria-pressed')).toBe('true')
  })

  it('pages the delta by its cursor', async () => {
    const { user, fakeApi } = setup()

    await loaded(versionsTable)
    await user.click(screen.getByRole('button', { name: text.versions.show.replace('{{seq}}', '2') }))
    await loaded(deltaTable)
    const pagers = screen.getAllByRole('button', { name: esMX.common.pager.next })
    await user.click(pagers[pagers.length - 1] as HTMLElement)

    expect(await screen.findByText('Cuarto Nombre')).toBeTruthy()
    expect(fakeApi.queries(`GET ${BASE}/versions/v2/delta`).map((query) => query.get('cursor'))).toEqual([null, 'QDi.003'])
  })

  it('opens an entry from its name, with its names, dates, nationalities, programmes and identifiers', async () => {
    const { user, fakeApi } = setup()

    await loaded(versionsTable)
    await user.click(screen.getByRole('button', { name: text.versions.show.replace('{{seq}}', '2') }))
    await loaded(deltaTable)
    await user.click(screen.getByRole('button', { name: 'Nombre Nuevo' }))

    const sheet = await screen.findByRole('dialog')
    await waitFor(() => expect(within(sheet).getByText('Alias Uno')).toBeTruthy())
    const entry = within(sheet)
    expect(entry.getAllByText('Nombre Nuevo').length).toBeGreaterThan(0)
    expect(entry.getByText('1970-03')).toBeTruthy()
    expect(entry.getByText('MX, AF')).toBeTruthy()
    expect(entry.getByText('QDi')).toBeTruthy()
    // Enum values read as words, never as the raw API values.
    expect(entry.getByText(text.entityType.PERSON)).toBeTruthy()
    expect(entry.getByText(`${text.idType.PASSPORT} P-123 (AF)`)).toBeTruthy()
    expect(entry.queryByText(/PASSPORT|PERSON/)).toBeNull()
    expect(fakeApi.calls(`GET ${BASE}/entries/e-new-2`)).toHaveLength(1)
  })
})
