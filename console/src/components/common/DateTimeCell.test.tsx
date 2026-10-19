// @vitest-environment jsdom
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { formatDateTime, formatShortDate, formatTime } from '@/lib/format'
import { DateTimeCell } from './DateTimeCell'

const AT = '2026-10-18T17:43:07.123456Z'

describe('DateTimeCell', () => {
  it('shows the date over the time, in a time element carrying the exact instant', () => {
    const { container } = render(<DateTimeCell value={AT} />)

    const cell = container.querySelector('time')
    expect(cell?.getAttribute('datetime')).toBe(AT)
    expect(cell?.textContent).toBe(`${formatShortDate(AT, 'es-MX')}${formatTime(AT, 'es-MX')}`)
    expect(cell?.querySelectorAll('span')).toHaveLength(2) // two lines
  })

  it('offers the whole date and time as its tooltip', () => {
    const { container } = render(<DateTimeCell value={AT} />)

    expect(container.querySelector('time')?.getAttribute('title')).toBe(formatDateTime(AT, 'es-MX'))
  })

  it('follows the language', async () => {
    await i18n.changeLanguage('en')
    const { container } = render(<DateTimeCell value={AT} />)

    expect(container.querySelector('time')?.textContent).toBe(`${formatShortDate(AT, 'en')}${formatTime(AT, 'en')}`)
  })
})
