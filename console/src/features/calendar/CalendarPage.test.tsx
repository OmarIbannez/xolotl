// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DueResponse, HolidayScope, HolidaysResponse } from '@/api/types'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { formatDateTime } from '@/lib/format'
import { fakeApi, refuse, resetFakeApi, respond } from '@/test/fake-api'
import { renderWithProviders } from '@/test/render'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { CalendarPage } from './CalendarPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: (await import('@/test/fake-api')).fakeApi,
}))

const text = esMX.calendar

function holidays(scope: HolidayScope, year: number): HolidaysResponse {
  const items =
    scope === 'CNBV'
      ? [
          { day: `${year}-04-02`, name: 'Jueves Santo', source: `CNBV ${year}` },
          { day: `${year}-04-03`, name: 'Viernes Santo', source: `CNBV ${year}` },
        ]
      : [{ day: `${year}-01-01`, name: 'Año Nuevo', source: 'LFT art. 74' }]
  return { country: 'MX', scope, year, items }
}

const ROR = { kind: 'NTH_BUSINESS_DAY_OF', params: { month_offset: 1, n: 10 }, tz: 'America/Mexico_City' } as const
const DUE = '2026-01-15T23:59:59-06:00'

function due(): DueResponse {
  return { due_at: DUE, rule: ROR, anchor: '2025-12-31T23:59:00-06:00', scope: 'CNBV' }
}

function serve() {
  fakeApi.GET.mockImplementation((path, init) => {
    const query = init?.params?.query ?? {}
    if (path === '/calendar/holidays') return respond(holidays(query.scope as HolidayScope, query.year as number))
    if (path === '/calendar/due') return respond(due())
    return refuse(404, 'NOT_FOUND')
  })
}

const callsTo = (route: string) => fakeApi.GET.mock.calls.filter(([path]) => path === route)

/** A table once its rows have arrived (the loading table is another element). */
async function loaded(name: string): Promise<HTMLElement> {
  await waitFor(() => expect(screen.getByRole('table', { name }).getAttribute('aria-busy')).toBeNull())
  return screen.getByRole('table', { name })
}

beforeEach(() => {
  resetFakeApi()
  stubPointerCapture()
  vi.useFakeTimers({ now: new Date('2026-10-19T12:00:00Z'), toFake: ['Date'] })
  serve()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CalendarPage: holidays', () => {
  it('opens on the national days of this year', async () => {
    renderWithProviders(<CalendarPage />)

    const table = await loaded(`${text.holidays.label} NATIONAL`)
    expect(within(table).getByRole('cell', { name: 'Año Nuevo' })).toBeTruthy()
    expect(callsTo('/calendar/holidays')[0]?.[1]?.params?.query).toEqual({ scope: 'NATIONAL', year: 2026 })
  })

  it('lists the CNBV days of the CNBV tab', async () => {
    const user = userEvent.setup()
    renderWithProviders(<CalendarPage />)
    await loaded(`${text.holidays.label} NATIONAL`)

    await user.click(screen.getByRole('tab', { name: 'CNBV' }))

    const table = await loaded(`${text.holidays.label} CNBV`)
    const row = within(within(table).getByRole('row', { name: /Jueves Santo/ }))
    expect(row.getByRole('cell', { name: '2026-04-02' })).toBeTruthy()
    expect(row.getByRole('cell', { name: 'CNBV 2026' })).toBeTruthy()
    expect(callsTo('/calendar/holidays').at(-1)?.[1]?.params?.query).toEqual({ scope: 'CNBV', year: 2026 })
  })

  it('asks for the year chosen', async () => {
    const user = userEvent.setup()
    renderWithProviders(<CalendarPage />)
    await loaded(`${text.holidays.label} NATIONAL`)

    await chooseOption(user, screen.getByRole('combobox', { name: text.year }), '2027')

    await waitFor(() =>
      expect(callsTo('/calendar/holidays').at(-1)?.[1]?.params?.query).toEqual({ scope: 'NATIONAL', year: 2027 }),
    )
  })
})

describe('CalendarPage: the deadline evaluator', () => {
  it('asks nothing until submitted, then shows when the rule falls due', async () => {
    const user = userEvent.setup()
    renderWithProviders(<CalendarPage />)
    await loaded(`${text.holidays.label} NATIONAL`)

    fireEvent.change(screen.getByLabelText(text.evaluator.anchor), { target: { value: '2025-12-31T23:59' } })
    expect(callsTo('/calendar/due')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: text.evaluator.submit }))

    expect(await screen.findByText(formatDateTime(DUE, 'es-MX'))).toBeTruthy()
    const query = callsTo('/calendar/due')[0]?.[1]?.params?.query
    expect(query).toEqual({ rule: JSON.stringify(ROR), anchor: '2025-12-31T23:59:00-06:00', scope: 'CNBV' })
    expect(JSON.parse(String(query?.rule))).toEqual(ROR)
    // the rule sent is shown with the answer
    expect(screen.getByText(/"month_offset": 1/)).toBeTruthy()
  })

  it('refuses a day of 31 before asking the API', async () => {
    const user = userEvent.setup()
    renderWithProviders(<CalendarPage />)
    await loaded(`${text.holidays.label} NATIONAL`)

    await chooseOption(user, screen.getByRole('combobox', { name: text.evaluator.kind }), 'DAY_OF_NEXT_MONTH')
    const day = screen.getByLabelText(text.evaluator.day)
    await user.clear(day)
    await user.type(day, '31')
    await user.click(screen.getByRole('button', { name: text.evaluator.submit }))

    expect(await screen.findByText(esMX.validation.positiveInteger)).toBeTruthy()
    expect(callsTo('/calendar/due')).toHaveLength(0)
  })

  it('shows a refusal of the API as an error', async () => {
    fakeApi.GET.mockImplementation((path, init) => {
      if (path === '/calendar/due') return refuse(422, 'VALIDATION_FAILED')
      return respond(holidays((init?.params?.query?.scope ?? 'NATIONAL') as HolidayScope, 2026))
    })
    const user = userEvent.setup()
    renderWithProviders(<CalendarPage />)
    await loaded(`${text.holidays.label} NATIONAL`)

    await user.click(screen.getByRole('button', { name: text.evaluator.submit }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(esMX.errors.VALIDATION_FAILED)
  })
})

it('follows the language switcher', async () => {
  await i18n.changeLanguage('en')
  renderWithProviders(<CalendarPage />)

  expect(await screen.findByRole('heading', { name: en.calendar.title, level: 1 })).toBeTruthy()
  expect(screen.getByRole('button', { name: en.calendar.evaluator.submit })).toBeTruthy()
})
