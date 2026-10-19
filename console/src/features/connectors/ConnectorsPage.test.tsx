// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConnectorList, ConnectorResponse, ConnectorUsage } from '@/api/types'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { fakeApi, pending, refuse, resetFakeApi, respond } from '@/test/fake-api'
import { formatShortDate } from '@/lib/format'
import { makeAppQueryClient, renderWithProviders } from '@/test/render'
import { ConnectorsPage } from './ConnectorsPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const text = esMX.connectors

function connector(overrides: Partial<ConnectorResponse> = {}): ConnectorResponse {
  return {
    name: 'sat_rfc',
    check_types: ['RFC_VALIDATION'],
    mode: 'SANDBOX',
    routes: [{ check_type: 'RFC_VALIDATION', country: 'MX', position: 'PRIMARY' }],
    circuit_state: 'CLOSED',
    credential_modes: [],
    ...overrides,
  }
}

function connectorList(items?: ConnectorResponse[]): ConnectorList {
  return {
    items: items ?? [
      connector(),
      connector({
        name: 'banxico_sie',
        check_types: ['UNIT_VALUE'],
        routes: [{ check_type: 'UNIT_VALUE', country: 'MX', position: 'PRIMARY' }],
        credential_modes: ['LIVE'],
      }),
    ],
  }
}

function usage(items?: ConnectorUsage['items']): ConnectorUsage {
  return {
    start: '2026-10-13',
    end: '2026-10-19',
    items: items ?? [
      {
        day: '2026-10-18',
        connector: 'sat_rfc',
        calls: 3,
        cost_micros: 2000,
        outcomes: { PASS: 2, TIMEOUT: 1 },
      },
      {
        day: '2026-10-19',
        connector: 'banxico_sie',
        calls: 1,
        cost_micros: 0,
        outcomes: { PASS: 1 },
      },
    ],
  }
}

function serve({ list = connectorList(), use = usage() }: { list?: ConnectorList; use?: ConnectorUsage } = {}) {
  fakeApi.GET.mockImplementation((path) => {
    if (path === '/connectors') return respond(list)
    if (path === '/connectors/usage') return respond(use)
    return refuse(404, 'NOT_FOUND')
  })
}

const listCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/connectors')
const usageCalls = () => fakeApi.GET.mock.calls.filter(([path]) => path === '/connectors/usage')
const connectorsTable = () => screen.getByRole('table', { name: text.table.label })
const usageTable = () => screen.getByRole('table', { name: text.usage.label })
/** The table once its rows have arrived (the loading table is another element). */
async function loaded(table: () => HTMLElement): Promise<HTMLElement> {
  await waitFor(() => expect(table().getAttribute('aria-busy')).toBeNull())
  return table()
}
/** The row of a connector once the list has loaded. */
const connectorRow = async (name: RegExp) => within(within(await loaded(connectorsTable)).getByRole('row', { name }))
/** The usage row of a connector once the usage has loaded. */
const usageRow = async (name: RegExp) => within(await loaded(usageTable)).getByRole('row', { name })

beforeEach(() => {
  resetFakeApi()
  vi.useFakeTimers({ now: new Date('2026-10-19T12:00:00Z'), toFake: ['Date'] })
  serve()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ConnectorsPage: states', () => {
  it('shows the skeleton while loading, then the connectors with their badges', async () => {
    fakeApi.GET.mockImplementation(() => pending())
    const { unmount } = renderWithProviders(<ConnectorsPage />)
    expect(connectorsTable().getAttribute('aria-busy')).toBe('true')
    unmount()

    serve()
    renderWithProviders(<ConnectorsPage />)

    const row = await connectorRow(/sat_rfc/)
    expect(row.getByText(text.circuit.CLOSED)).toBeTruthy()
    expect(row.getByText(text.mode.SANDBOX)).toBeTruthy()
    expect(row.getByText(text.checkType.RFC_VALIDATION)).toBeTruthy()
    expect(row.getByText(`RFC_VALIDATION · MX · ${text.route.PRIMARY}`)).toBeTruthy()
    expect(row.getByText(text.credentials.none)).toBeTruthy()
    const banxico = await connectorRow(/banxico_sie/)
    expect(banxico.getByText('Configuradas: LIVE')).toBeTruthy()
  })

  it('shows an empty block when no connector is configured', async () => {
    serve({ list: connectorList([]) })
    renderWithProviders(<ConnectorsPage />)

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.queryByRole('table', { name: text.table.label })).toBeNull()
  })

  it('shows the failure with its reference and retries', async () => {
    fakeApi.GET.mockImplementation((path) =>
      path === '/connectors' ? refuse(500, 'INTERNAL') : respond(usage()),
    )
    const user = userEvent.setup()
    renderWithProviders(<ConnectorsPage />)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.INTERNAL)
    expect(alert.textContent).toContain('req-test')

    serve()
    await user.click(within(alert).getByRole('button', { name: esMX.common.retry }))

    expect(await connectorRow(/sat_rfc/)).toBeTruthy()
    expect(listCalls()).toHaveLength(2)
  })

  it('renders an OPEN circuit as critical', async () => {
    serve({ list: connectorList([connector({ circuit_state: 'OPEN' })]) })
    renderWithProviders(<ConnectorsPage />)

    const badge = await screen.findByText(text.circuit.OPEN)
    expect(badge.closest('[data-variant]')?.getAttribute('data-variant')).toBe('critical')
  })
})

describe('ConnectorsPage: usage', () => {
  it('asks for the last 7 days by default and 30 on request', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ConnectorsPage />)
    await screen.findByRole('table', { name: text.usage.label })

    const week = screen.getByRole('button', { name: text.usage.range7 })
    const month = screen.getByRole('button', { name: text.usage.range30 })
    expect(week.getAttribute('aria-pressed')).toBe('true')
    expect(month.getAttribute('aria-pressed')).toBe('false')

    await user.click(month)

    await waitFor(() => expect(usageCalls()).toHaveLength(2))
    expect(usageCalls().map(([, init]) => init?.params?.query)).toEqual([
      { from: '2026-10-13', to: '2026-10-19' },
      { from: '2026-09-20', to: '2026-10-19' },
    ])
    expect(month.getAttribute('aria-pressed')).toBe('true')
    expect(week.getAttribute('aria-pressed')).toBe('false')
  })

  it('lists calls, failures and cost per day and connector', async () => {
    renderWithProviders(<ConnectorsPage />)
    const row = await usageRow(/sat_rfc/)

    expect(
      within(usageTable())
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual([text.usage.day, text.usage.connector, text.usage.calls, text.usage.failed, text.usage.cost])
    const cells = within(row).getAllByRole('cell').map((cell) => cell.textContent)
    // the UTC day itself, not the evening before in Mexico City
    expect(cells).toEqual([formatShortDate(new Date(2026, 9, 18), 'es-MX'), 'sat_rfc', '3', '1', '0.002'])
    const meter = row.querySelector('meter')
    expect(meter?.getAttribute('value')).toBe('3')
    expect(meter?.getAttribute('max')).toBe('3')
  })

  it('shows an empty message for a quiet period', async () => {
    serve({ use: usage([]) })
    renderWithProviders(<ConnectorsPage />)

    expect(await screen.findByText(text.usage.empty)).toBeTruthy()
    expect(screen.queryByRole('table', { name: text.usage.label })).toBeNull()
  })
})

describe('ConnectorsPage: access', () => {
  it('makes exactly one request and shows the forbidden page for a 403', async () => {
    fakeApi.GET.mockImplementation(() => refuse(403, 'ROLE_FORBIDDEN'))
    renderWithProviders(<ConnectorsPage />, { queryClient: makeAppQueryClient() })

    const alerts = await screen.findAllByRole('alert')
    expect(alerts.length).toBeGreaterThan(0)
    for (const alert of alerts) {
      expect(alert.textContent).toContain(esMX.errors.ROLE_FORBIDDEN)
      expect(alert.textContent).not.toContain('English detail')
    }
    expect(screen.queryByRole('table')).toBeNull()
    expect(listCalls()).toHaveLength(1)
    expect(usageCalls()).toHaveLength(1)
  })
})

it('follows the language switcher', async () => {
  await i18n.changeLanguage('en')
  renderWithProviders(<ConnectorsPage />)

  expect(await screen.findByRole('heading', { name: en.connectors.title })).toBeTruthy()
  const row = within(await screen.findByRole('row', { name: /sat_rfc.*Closed/ }))
  expect(row.getByText(en.connectors.circuit.CLOSED)).toBeTruthy()
  expect(row.getByText(`RFC_VALIDATION · MX · ${en.connectors.route.PRIMARY}`)).toBeTruthy()
  expect(screen.getByRole('button', { name: en.connectors.usage.range7 })).toBeTruthy()
})

it('shows names as text, never markup', async () => {
  const MARKUP = '<img src=x onerror="window.__pwned=1">'
  serve({
    list: connectorList([connector({ name: MARKUP })]),
    use: usage([{ day: '2026-10-19', connector: MARKUP, calls: 1, cost_micros: 0, outcomes: {} }]),
  })
  renderWithProviders(<ConnectorsPage />)

  await waitFor(() => expect(screen.getAllByText(MARKUP)).toHaveLength(2)) // the list and the usage
  expect(document.body.querySelector('img')).toBeNull()
  expect(Reflect.get(window, '__pwned')).toBeUndefined()
})
