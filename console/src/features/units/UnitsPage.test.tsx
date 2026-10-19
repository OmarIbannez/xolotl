// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UnitLookupResponse, UnitsResponse, UnitValuesResponse } from '@/api/types'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, refuse, resetFakeApi, respond } from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { UnitsPage } from './UnitsPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const text = esMX.units

const units: UnitsResponse = {
  items: [
    {
      unit_code: 'UDI',
      quote_ccy: 'MXN',
      daily: true,
      series_id: 'SP68257',
      latest_valid_from: '2026-01-21',
      latest_fetched_at: '2026-10-18T12:00:00Z',
      latest_source: 'banxico-sie',
    },
    {
      unit_code: 'UMA',
      quote_ccy: 'MXN',
      daily: false,
      series_id: null,
      latest_valid_from: '2026-02-01',
      latest_fetched_at: '2026-10-18T12:00:00Z',
      latest_source: 'seed:UMA.yaml',
    },
    {
      unit_code: 'USD_MXN_FIX',
      quote_ccy: 'MXN',
      daily: true,
      series_id: 'SF43718',
      latest_valid_from: null,
      latest_fetched_at: null,
      latest_source: null,
    },
  ],
}

function values(code: string): UnitValuesResponse {
  if (code !== 'USD_MXN_FIX') return { unit_code: code, items: [] }
  const row = { unit_code: code, valid_to: null, quote_ccy: 'MXN', source: 'banxico-sie' }
  return {
    unit_code: code,
    items: [
      { ...row, id: 11, valid_from: '2025-12-11', value: '17.9000', fetched_at: '2025-12-11T18:00:00Z', superseded_by: 12 },
      { ...row, id: 12, valid_from: '2025-12-11', value: '17.9123', fetched_at: '2025-12-12T18:00:00Z', superseded_by: null },
    ],
  }
}

const lookup: UnitLookupResponse = {
  unit_code: 'UDI',
  on: '2026-01-20',
  rule: 'LAST_DAY_PREV_MONTH',
  resolved_date: '2025-12-31',
  value: '8.52000000',
  quote_ccy: 'MXN',
  unit_value_id: 7,
  source: 'banxico-sie',
  fetched_at: '2026-01-01T18:00:00Z',
}

function serve({ lookupAnswer = () => respond(lookup) }: { lookupAnswer?: () => ReturnType<typeof respond> } = {}) {
  fakeApi.GET.mockImplementation((path, init) => {
    if (path === '/units') return respond(units)
    if (path === '/units/{code}/values') return respond(values(init?.params?.path?.code ?? ''))
    if (path === '/units/{code}') return lookupAnswer()
    return refuse(404, 'NOT_FOUND')
  })
}

/** A table once its rows have arrived (the loading table is another element). */
async function loaded(name: string): Promise<HTMLElement> {
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

const callsTo = (route: string) => fakeApi.GET.mock.calls.filter(([path]) => path === route)

beforeEach(() => {
  resetFakeApi()
  stubPointerCapture()
  vi.useFakeTimers({ now: new Date('2026-10-19T12:00:00Z'), toFake: ['Date'] })
  serve()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('UnitsPage', () => {
  it('lists every unit with its latest value date, last fetch and source', async () => {
    renderWithProviders(<UnitsPage />)

    const table = await loaded(text.table.label)
    const udi = within(within(table).getByRole('row', { name: /UDI/ }))
    expect(udi.getByRole('cell', { name: '2026-01-21' })).toBeTruthy()
    expect(udi.getByRole('cell', { name: 'banxico-sie' })).toBeTruthy()
    expect(within(table).getByRole('cell', { name: 'seed:UMA.yaml' })).toBeTruthy()
    // a unit with nothing stored yet still has its row
    expect(within(table).getByRole('row', { name: /USD_MXN_FIX/ })).toBeTruthy()
  })

  it('opens a unit tab on its last 90 days and marks a corrected row', async () => {
    const user = userEvent.setup()
    renderWithProviders(<UnitsPage />)

    await user.click(await screen.findByRole('tab', { name: 'USD_MXN_FIX' }))

    const history = await loaded(`${text.history.title} USD_MXN_FIX`)
    expect(within(history).getAllByRole('row')).toHaveLength(3)
    const old = within(within(history).getByRole('row', { name: /17\.9000/ }))
    const badge = old.getByText(text.corrected).closest('[data-variant]')
    expect(badge?.getAttribute('data-variant')).toBe('muted')
    const current = within(within(history).getByRole('row', { name: /17\.9123/ }))
    expect(current.queryByText(text.corrected)).toBeNull()

    const last = callsTo('/units/{code}/values').at(-1)?.[1]?.params
    expect(last).toEqual({ path: { code: 'USD_MXN_FIX' }, query: { from: '2026-07-21', to: '2026-10-19' } })
  })

  it('asks for the history between the dates typed', async () => {
    renderWithProviders(<UnitsPage />)
    await screen.findByRole('table', { name: `${text.history.title} UDI` })

    fireEvent.change(screen.getByLabelText(text.history.from), { target: { value: '2025-12-01' } })
    fireEvent.change(screen.getByLabelText(text.history.to), { target: { value: '2025-12-31' } })

    await waitFor(() =>
      expect(callsTo('/units/{code}/values').at(-1)?.[1]?.params?.query).toEqual({ from: '2025-12-01', to: '2025-12-31' }),
    )
  })

  it('looks up a value only when asked, with the date and rule chosen', async () => {
    const user = userEvent.setup()
    renderWithProviders(<UnitsPage />)
    await screen.findByRole('table', { name: `${text.history.title} UDI` })
    expect(callsTo('/units/{code}')).toHaveLength(0)

    fireEvent.change(screen.getByLabelText(text.lookup.on), { target: { value: '2026-01-20' } })
    await chooseOption(user, screen.getByRole('combobox', { name: text.lookup.rule }), 'LAST_DAY_PREV_MONTH')
    await user.click(screen.getByRole('button', { name: text.lookup.submit }))

    expect(await screen.findByText('8.52000000 MXN (valor del 2025-12-31)')).toBeTruthy()
    expect(callsTo('/units/{code}')[0]?.[1]?.params).toEqual({
      path: { code: 'UDI' },
      query: { on: '2026-01-20', rule: 'LAST_DAY_PREV_MONTH' },
    })
  })

  it('says a missing value is missing on a 503 UNIT_VALUE_MISSING', async () => {
    serve({ lookupAnswer: () => refuse(503, 'UNIT_VALUE_MISSING') })
    const user = userEvent.setup()
    renderWithProviders(<UnitsPage />)
    await screen.findByRole('table', { name: `${text.history.title} UDI` })

    await user.click(screen.getByRole('button', { name: text.lookup.submit }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.UNIT_VALUE_MISSING)
  })

  it('follows the language switcher', async () => {
    await i18n.changeLanguage('en')
    renderWithProviders(<UnitsPage />)

    expect(await screen.findByRole('heading', { name: en.units.title, level: 1 })).toBeTruthy()
    expect(await screen.findByRole('table', { name: en.units.table.label })).toBeTruthy()
  })
})
