import { screen } from '@testing-library/react'
import type { UserEvent } from '@testing-library/user-event'

/**
 * Radix Select reads pointer capture and scrolls the chosen option into view, which jsdom
 * lacks. Call from a component test's `beforeEach` before opening a `Select`.
 */
export function stubPointerCapture(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>
  proto.hasPointerCapture ??= () => false
  proto.setPointerCapture ??= () => undefined
  proto.releasePointerCapture ??= () => undefined
  proto.scrollIntoView ??= () => undefined
}

/** Open a `Select` by its trigger (role combobox) and pick the option named `name`. */
export async function chooseOption(user: UserEvent, trigger: HTMLElement, name: string): Promise<void> {
  await user.click(trigger)
  await user.click(await screen.findByRole('option', { name }))
}
