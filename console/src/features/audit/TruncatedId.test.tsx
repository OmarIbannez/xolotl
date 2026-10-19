// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TruncatedId } from './TruncatedId'

const UUID = '01a151a8-f995-7028-8b2f-9c01d3e5a7b2'

describe('TruncatedId', () => {
  it('keeps the whole identifier in the text and in the hover title', () => {
    render(<TruncatedId value={UUID} />)

    const id = screen.getByTitle(UUID)
    expect(id).toHaveTextContent(UUID) // selectable, searchable, readable by assistive technology
    expect(id.textContent).toBe(UUID)
  })

  it('lets the front shrink and always shows the end, which is the part that tells ids apart', () => {
    render(<TruncatedId value={UUID} />)

    const [head, tail] = Array.from(screen.getByTitle(UUID).children)
    expect(head).toHaveClass('truncate', 'min-w-0')
    expect(head?.textContent).toBe('01a151a8-f995-7028-8b2f-9c01')
    expect(tail).toHaveClass('shrink-0')
    expect(tail?.textContent).toBe('d3e5a7b2')
  })

  it('has no front to cut when the identifier is short', () => {
    render(<TruncatedId value="auth" />)

    const [head, tail] = Array.from(screen.getByTitle('auth').children)
    expect(head?.textContent).toBe('')
    expect(tail?.textContent).toBe('auth')
  })

  it('never splits a character in half', () => {
    const value = `abc${'😀'.repeat(9)}`
    render(<TruncatedId value={value} />)

    const [head, tail] = Array.from(screen.getByTitle(value).children)
    expect(Array.from(tail?.textContent ?? '')).toHaveLength(8)
    expect(`${head?.textContent}${tail?.textContent}`).toBe(value)
    expect(tail?.textContent).toBe('😀'.repeat(8))
  })

  it('prints markup as text', () => {
    const value = '<img src=x onerror=alert(1)>0123456789'
    const { container } = render(<TruncatedId value={value} />)

    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTitle(value).textContent).toBe(value)
  })

  it('takes extra classes for its place in a cell', () => {
    render(<TruncatedId value={UUID} className="text-muted-foreground" />)

    expect(screen.getByTitle(UUID)).toHaveClass('flex', 'font-mono', 'text-muted-foreground')
  })
})
