// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { CursorPager } from './CursorPager'

const NEXT = esMX.common.pager.next
const PREVIOUS = esMX.common.pager.previous

// A three-page list: the first page has no cursor; each page says where the next one is.
const NEXT_CURSOR: Record<string, string | null> = { first: 'c2', c2: 'c3', c3: null }

/** What a page does with the pager: it owns the cursor and fetches whatever it is told. */
function Harness({ onChange, busy = false }: { onChange: (cursor: string | undefined) => void; busy?: boolean }) {
  const [cursor, setCursor] = useState<string | undefined>()
  return (
    <>
      <p data-testid="showing">{cursor ?? 'first'}</p>
      <CursorPager
        cursor={cursor}
        nextCursor={NEXT_CURSOR[cursor ?? 'first']}
        busy={busy}
        onChange={(next) => {
          onChange(next)
          setCursor(next)
        }}
      />
      <button type="button" onClick={() => setCursor(undefined)}>
        jump to first page
      </button>
    </>
  )
}

const showing = () => screen.getByTestId('showing').textContent
const button = (name: string) => screen.getByRole('button', { name })

describe('CursorPager', () => {
  it('starts on page 1 with Previous disabled and Next enabled', () => {
    render(<Harness onChange={vi.fn()} />)

    expect(screen.getByText('Página 1')).toBeTruthy()
    expect(button(PREVIOUS)).toHaveProperty('disabled', true)
    expect(button(NEXT)).toHaveProperty('disabled', false)
  })

  it('asks for the next cursor and enables Previous', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)

    await userEvent.setup().click(button(NEXT))

    expect(onChange).toHaveBeenCalledExactlyOnceWith('c2')
    expect(showing()).toBe('c2')
    expect(screen.getByText('Página 2')).toBeTruthy()
    expect(button(PREVIOUS)).toHaveProperty('disabled', false)
  })

  it('walks forward and back along the cursors it went through, ending on the first page', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} />)

    await user.click(button(NEXT))
    await user.click(button(NEXT))
    expect(screen.getByText('Página 3')).toBeTruthy()
    await user.click(button(PREVIOUS))
    await user.click(button(PREVIOUS))

    expect(onChange.mock.calls.map(([cursor]) => cursor)).toEqual(['c2', 'c3', 'c2', undefined])
    expect(showing()).toBe('first')
    expect(screen.getByText('Página 1')).toBeTruthy()
    expect(button(PREVIOUS)).toHaveProperty('disabled', true)
  })

  it('disables Next on the last page, where the API gives no next cursor', async () => {
    const user = userEvent.setup()
    render(<Harness onChange={vi.fn()} />)

    await user.click(button(NEXT))
    await user.click(button(NEXT))

    expect(showing()).toBe('c3')
    expect(button(NEXT)).toHaveProperty('disabled', true)
    expect(button(PREVIOUS)).toHaveProperty('disabled', false)
  })

  it('treats an absent next cursor like a null one', () => {
    render(<CursorPager cursor={undefined} nextCursor={undefined} onChange={vi.fn()} />)

    expect(button(NEXT)).toHaveProperty('disabled', true)
  })

  it('disables both buttons while a page is loading, so a click cannot skip one', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} busy />)

    expect(button(NEXT)).toHaveProperty('disabled', true)
    expect(button(PREVIOUS)).toHaveProperty('disabled', true)
    await user.click(button(NEXT))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('starts over when the page moves elsewhere on its own (back to the first page after a create)', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} />)
    await user.click(button(NEXT))
    await user.click(button(NEXT))

    await user.click(screen.getByRole('button', { name: 'jump to first page' }))

    expect(showing()).toBe('first')
    expect(screen.getByText('Página 1')).toBeTruthy()
    expect(button(PREVIOUS)).toHaveProperty('disabled', true)
    // and it paginates again from there
    await user.click(button(NEXT))
    expect(screen.getByText('Página 2')).toBeTruthy()
    await user.click(button(PREVIOUS))
    expect(showing()).toBe('first')
  })

  it('labels the navigation and its buttons from the catalogue', () => {
    render(<CursorPager cursor={undefined} nextCursor="c2" onChange={vi.fn()} />)

    expect(screen.getByRole('navigation', { name: esMX.common.pager.label })).toBeTruthy()
  })
})
