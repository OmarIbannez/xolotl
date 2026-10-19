// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { OneTimeSecretDialog } from './OneTimeSecretDialog'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const SECRET = 'xk_sbx_1a2b3c4d_0123456789abcdef0123456789abcdef'
const COPY = esMX.common.oneTime.copy
const COPIED = esMX.common.oneTime.copied
const ACKNOWLEDGE = esMX.common.oneTime.acknowledge
const CLOSE = esMX.common.close

function setup(onClose = vi.fn()) {
  const user = userEvent.setup() // also installs a clipboard stub on navigator
  render(<OneTimeSecretDialog title="Llave creada" secret={SECRET} hint="Guárdala ahora." onClose={onClose} />)
  return { user, onClose }
}

const closeButton = () => screen.getByRole('button', { name: CLOSE })

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('OneTimeSecretDialog', () => {
  it('shows the title, the hint and the secret in monospace', () => {
    setup()

    const dialog = screen.getByRole('dialog', { name: 'Llave creada' })
    expect(dialog.textContent).toContain('Guárdala ahora.')
    const secret = screen.getByText(SECRET)
    expect(secret.tagName).toBe('CODE')
    expect(secret.className).toContain('font-mono')
  })

  it('keeps Close disabled until the user says they saved the secret', async () => {
    const { user, onClose } = setup()

    expect(closeButton()).toHaveProperty('disabled', true)
    expect(screen.getByText(esMX.common.oneTime.acknowledgeRequired)).toBeTruthy()
    await user.click(closeButton())
    expect(onClose).not.toHaveBeenCalled()

    await user.click(screen.getByRole('checkbox', { name: ACKNOWLEDGE }))

    expect(closeButton()).toHaveProperty('disabled', false)
    await user.click(closeButton())
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('disables Close again if the acknowledgement is withdrawn', async () => {
    const { user } = setup()
    const checkbox = screen.getByRole('checkbox', { name: ACKNOWLEDGE })

    await user.click(checkbox)
    await user.click(checkbox)

    expect(closeButton()).toHaveProperty('disabled', true)
  })

  it('does not close on Escape until acknowledged, then does', async () => {
    const { user, onClose } = setup()

    await user.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()

    await user.click(screen.getByRole('checkbox', { name: ACKNOWLEDGE }))
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close on a click outside until acknowledged', async () => {
    const { onClose } = setup()
    await new Promise((resolve) => setTimeout(resolve, 10)) // Radix listens for outside clicks from the next tick

    fireEvent.pointerDown(document.body)
    fireEvent.pointerUp(document.body)
    fireEvent.click(document.body)

    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('has no close button in the corner that could skip the acknowledgement', () => {
    setup()

    // The only "Cerrar" is the footer button that the checkbox gates.
    expect(screen.getAllByRole('button', { name: CLOSE })).toHaveLength(1)
  })

  it('copies the secret to the clipboard and confirms with a toast and a label', async () => {
    const { user } = setup()

    await user.click(screen.getByRole('button', { name: COPY }))

    expect(await navigator.clipboard.readText()).toBe(SECRET)
    expect(toast.success).toHaveBeenCalledExactlyOnceWith(esMX.common.oneTime.copiedToast)
    expect(await screen.findByRole('button', { name: COPIED })).toBeTruthy()
  })

  it('says so, and leaves the text to copy by hand, when the clipboard refuses', async () => {
    const { user } = setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))

    await user.click(screen.getByRole('button', { name: COPY }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledExactlyOnceWith(esMX.common.oneTime.copyFailed))
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: COPY })).toBeTruthy() // still "Copiar", not "Copiado"
    expect(screen.getByText(SECRET)).toBeTruthy()
  })

  it('says so when there is no clipboard at all (an insecure page)', async () => {
    const { user } = setup()
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined })

    await user.click(screen.getByRole('button', { name: COPY }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    vi.unstubAllGlobals()
  })

  it('stores and logs the secret nowhere', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const logs = (['log', 'info', 'warn', 'error', 'debug'] as const).map((method) => vi.spyOn(console, method))
    const { user, onClose } = setup()

    await user.click(screen.getByRole('button', { name: COPY }))
    await user.click(screen.getByRole('checkbox', { name: ACKNOWLEDGE }))
    await user.click(closeButton())

    expect(onClose).toHaveBeenCalled()
    expect(setItem.mock.calls.some(([, value]) => String(value).includes(SECRET))).toBe(false)
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(SECRET)
    for (const log of logs) {
      expect(log.mock.calls.flat().some((arg) => String(arg).includes(SECRET))).toBe(false)
    }
  })
})
