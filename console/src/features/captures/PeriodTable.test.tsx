// @vitest-environment jsdom
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { renderApp } from '@/test/render-app'
import { closedPeriod, EXTRA_ID, MISSING_ID, period } from './fixtures'
import { PeriodTable } from './PeriodTable'

const text = esMX.captures

function setup(rows = [closedPeriod(), period()]) {
  const onClose = vi.fn()
  const onShowEvents = vi.fn()
  const rendered = renderApp(
    <PeriodTable rows={rows} loading={false} today="2026-10-22" canClose onClose={onClose} onShowEvents={onShowEvents} />,
  )
  return { ...rendered, onClose, onShowEvents }
}

describe('PeriodTable', () => {
  it('discrepancy ids are disclosed', async () => {
    const { user } = setup()

    const row = within(screen.getByText('2026-08').closest('tr') as HTMLElement)
    expect(row.getByText(text.status.CLOSED)).toBeTruthy()
    expect(row.queryByRole('link', { name: MISSING_ID })).toBeNull()

    await user.click(row.getByRole('button', { name: text.discrepancies.show.replace('{{count}}', '2') }))

    expect(row.getByText(text.discrepancies.missing)).toBeTruthy()
    expect(row.getByText(text.discrepancies.extra)).toBeTruthy()
    expect(row.getByRole('link', { name: MISSING_ID }).getAttribute('href')).toBe(`/transactions/${MISSING_ID}`)
    expect(row.getByRole('link', { name: EXTRA_ID }).getAttribute('href')).toBe(`/transactions/${EXTRA_ID}`)
  })

  it('shows the counts of an open period not reconciled yet', () => {
    setup()

    const row = within(screen.getByText('2026-09').closest('tr') as HTMLElement)
    expect(row.getByText(text.status.OPEN)).toBeTruthy()
    // The period's days as dates, not ISO strings.
    expect(row.getByText('1 sep 2026 a 30 sep 2026')).toBeTruthy()
    expect(row.getByText('2')).toBeTruthy()
    expect(row.getByText(text.discrepancies.pending)).toBeTruthy()
  })

  it('hands the chosen row to the close and the captures list', async () => {
    const { user, onClose, onShowEvents } = setup()

    const row = within(screen.getByText('2026-09').closest('tr') as HTMLElement)
    await user.click(row.getByRole('button', { name: 'Cerrar periodo ITF_ROR_CASH 2026-09' }))
    await user.click(row.getByRole('button', { name: 'Ver capturas de ITF_ROR_CASH 2026-09' }))

    expect(onClose).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }))
    expect(onShowEvents).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }))
  })
})
