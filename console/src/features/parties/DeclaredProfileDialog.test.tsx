// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { DeclaredProfileDialog } from './DeclaredProfileDialog'
import { customerRelationship } from './fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.parties.profile.dialog
const PATCH = 'PATCH /v1/relationships/r1'

function setup(declared: Record<string, unknown> | null = null, routes: FakeRoutes = {}) {
  const onClose = vi.fn()
  const rendered = renderApp(
    <DeclaredProfileDialog relationship={customerRelationship({ declared_profile: declared })} onClose={onClose} />,
    { me: makeMe({ roles: ['ANALYST'] }), routes: { [PATCH]: () => json(customerRelationship()), ...routes } },
  )
  return { ...rendered, onClose }
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe('DeclaredProfileDialog', () => {
  it('sends the amounts in minor units with the expected countries and instruments', async () => {
    const { user, fakeApi, onClose } = setup()

    await user.type(screen.getByLabelText(text.inflow), '125000.50')
    await user.type(screen.getByLabelText(text.outflow), '80,000')
    await user.type(screen.getByLabelText(text.countries), 'MX, US')
    await user.click(screen.getByRole('checkbox', { name: esMX.transactions.instrument.CASH }))
    await user.click(screen.getByRole('button', { name: text.submit }))

    await waitFor(() => expect(fakeApi.calls(PATCH)).toHaveLength(1))
    const [sent] = fakeApi.calls(PATCH)
    expect(await sent?.json()).toEqual({
      declared_profile: {
        monthly_inflow_minor: 12500050,
        monthly_outflow_minor: 8000000,
        currency: 'MXN',
        expected_countries: ['MX', 'US'],
        expected_instruments: ['CASH'],
      },
    })
    expect(sent?.headers.has('Idempotency-Key')).toBe(false)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalledWith(text.saved)
  })

  it('prefills the stored declaration', async () => {
    setup({
      monthly_inflow_minor: 1000000,
      monthly_outflow_minor: 5,
      currency: 'MXN',
      expected_countries: ['MX', 'GT'],
      expected_instruments: ['TRANSFER'],
    })

    expect((screen.getByLabelText(text.inflow) as HTMLInputElement).value).toBe('10000.00')
    expect((screen.getByLabelText(text.outflow) as HTMLInputElement).value).toBe('0.05')
    expect((screen.getByLabelText(text.countries) as HTMLInputElement).value).toBe('MX, GT')
    expect(screen.getByRole('checkbox', { name: esMX.transactions.instrument.TRANSFER }).getAttribute('aria-checked')).toBe('true')
  })

  it('refuses a negative amount without sending anything', async () => {
    const { user, fakeApi } = setup()

    await user.type(screen.getByLabelText(text.inflow), '-100')
    await user.type(screen.getByLabelText(text.outflow), '100')
    await user.click(screen.getByRole('button', { name: text.submit }))

    expect(await screen.findByText(esMX.validation.amountMxn)).toBeTruthy()
    expect(fakeApi.calls(PATCH)).toHaveLength(0)
  })

  it('keeps the dialog open with the reason when the API refuses', async () => {
    const { user, onClose } = setup(null, { [PATCH]: () => problem(403, 'ROLE_FORBIDDEN') })

    await user.type(screen.getByLabelText(text.inflow), '100')
    await user.type(screen.getByLabelText(text.outflow), '100')
    await user.click(screen.getByRole('button', { name: text.submit }))

    expect(await screen.findByText(esMX.errors.ROLE_FORBIDDEN)).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
  })

  // The console never shows the server's English `msg`; the field gets the translated message.
  it('shows a server refusal under the field it names and keeps the dialog open', async () => {
    const refusal = () =>
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Validation failed',
          status: 422,
          detail: 'invalid',
          instance: '/v1/relationships/r1',
          code: 'VALIDATION_FAILED',
          errors: [
            { loc: ['body', 'declared_profile', 'expected_countries', 0], msg: 'country not allowed', type: 'value_error' },
            { loc: ['body', 'declared_profile', 'monthly_inflow_minor'], msg: 'inflow too large', type: 'value_error' },
          ],
        }),
        { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
      )
    const { user, onClose } = setup(null, { [PATCH]: refusal })

    await user.type(screen.getByLabelText(text.inflow), '100')
    await user.type(screen.getByLabelText(text.outflow), '100')
    await user.type(screen.getByLabelText(text.countries), 'XX')
    await user.click(screen.getByRole('button', { name: text.submit }))

    const countries = screen.getByLabelText(text.countries)
    await waitFor(() => expect(countries.getAttribute('aria-invalid')).toBe('true'))
    expect(document.getElementById(countries.getAttribute('aria-describedby')?.split(' ').at(-1) ?? '')?.textContent).toBe(
      esMX.validation.invalid,
    )
    const inflow = screen.getByLabelText(text.inflow)
    expect(inflow.getAttribute('aria-invalid')).toBe('true')
    expect(document.getElementById(inflow.getAttribute('aria-describedby')?.split(' ').at(-1) ?? '')?.textContent).toBe(
      esMX.validation.invalid,
    )
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})
