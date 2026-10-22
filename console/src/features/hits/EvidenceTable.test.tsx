// @vitest-environment jsdom
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { EvidenceTable } from './EvidenceTable'
import { hitDetail } from './fixtures'

const text = esMX.hits

const rowsOf = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent))

describe('EvidenceTable', () => {
  it('lists the values the rule read beside the parameters it ran with, as the API sent them', () => {
    render(<EvidenceTable evidence={hitDetail().evidence} />)

    expect(rowsOf(screen.getByRole('table', { name: text.detail.aggregates }))).toEqual([
      ['near_count', '3', '—', 'P7D'],
      ['near_sum', '11500.00', 'USD', 'P7D'],
    ])
    expect(rowsOf(screen.getByRole('table', { name: text.detail.params }))).toEqual([
      ['min_count', '3'],
      ['near_ratio', '0.70'],
      ['threshold', '5000'],
      ['window', 'P7D'],
    ])
  })

  it('says so when the rule read no aggregate and ran without parameters', () => {
    render(<EvidenceTable evidence={{ aggregates: {}, params: {}, explanation: '', lists_hash: '' }} />)

    expect(screen.getByText(text.evidence.noAggregates)).toBeTruthy()
    expect(screen.getByText(text.evidence.noParams)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
