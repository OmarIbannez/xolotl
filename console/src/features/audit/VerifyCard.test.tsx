// @vitest-environment jsdom
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { verification } from './fixtures'
import { VerifyCard } from './VerifyCard'

const es = esMX.audit.verify
const CHECKED_AT = new Date('2026-10-18T16:24:05Z')

function show(overrides: Parameters<typeof verification>[0]) {
  render(<VerifyCard result={verification(overrides)} checkedAt={CHECKED_AT} />)
  return screen.getByRole('region')
}

describe('VerifyCard', () => {
  it('says the chain is intact, and how many events were walked', () => {
    const card = show({ valid: true, entries_checked: 1234 })

    expect(card).toHaveAccessibleName(es.valid.title)
    expect(card).toHaveAttribute('data-outcome', 'valid')
    expect(within(card).getByText(es.valid.body)).toBeInTheDocument()
    expect(within(card).getByText(es.entriesChecked)).toBeInTheDocument()
    expect(within(card).getByText('1,234')).toBeInTheDocument()
    expect(within(card).queryByText(es.broken.title)).toBeNull()
  })

  it('has its own words for a valid chain with no entries, and does not claim it was walked', () => {
    const card = show({ valid: true, entries_checked: 0 })

    expect(card).toHaveAccessibleName(es.empty.title)
    expect(card).toHaveAttribute('data-outcome', 'empty')
    expect(within(card).getByText(es.empty.body)).toBeInTheDocument()
    expect(within(card).queryByText(es.valid.title)).toBeNull()
    expect(within(card).queryByText(es.valid.body)).toBeNull()
    expect(within(card).queryByText(es.entriesChecked)).toBeNull()
  })

  it('names the first broken sequence number and the reason when the chain is broken', () => {
    const card = show({
      valid: false,
      entries_checked: 42,
      first_broken_seq: 1042,
      reason: 'content does not match its hash',
    })

    expect(card).toHaveAccessibleName(es.broken.title)
    expect(card).toHaveAttribute('data-outcome', 'broken')
    expect(within(card).getByText(es.broken.firstBrokenSeq)).toBeInTheDocument()
    expect(within(card).getByText('1042')).toBeInTheDocument() // a sequence number is not grouped
    expect(within(card).getByText(es.reason.content)).toBeInTheDocument()
    expect(within(card).getByText('42')).toBeInTheDocument()
    expect(within(card).queryByText(es.valid.title)).toBeNull()
  })

  it.each([
    ['sequence gap or duplicate', es.reason.sequence],
    ['prev_hash does not match the previous entry', es.reason.prevHash],
    ['content does not match its hash', es.reason.content],
  ])('translates the API reason "%s"', (reason, message) => {
    const card = show({ valid: false, entries_checked: 2, first_broken_seq: 2, reason })

    expect(within(card).getByText(message)).toBeInTheDocument()
    expect(within(card).queryByText(es.broken.rawReason)).toBeNull() // nothing left untranslated to show
  })

  it('keeps the API words visible when the reason is one it cannot translate', () => {
    const card = show({ valid: false, entries_checked: 2, first_broken_seq: 2, reason: 'a future reason' })

    expect(within(card).getByText(es.reason.unknown)).toBeInTheDocument()
    expect(within(card).getByText(es.broken.rawReason)).toBeInTheDocument()
    expect(within(card).getByText('a future reason')).toBeInTheDocument()
  })

  it('still reports a break that came without a sequence number or reason', () => {
    const card = show({ valid: false, entries_checked: 0, first_broken_seq: null, reason: null })

    expect(card).toHaveAccessibleName(es.broken.title)
    expect(within(card).queryByText(es.broken.firstBrokenSeq)).toBeNull()
    expect(within(card).getByText(es.reason.unknown)).toBeInTheDocument()
  })

  it('says when the check was made', () => {
    const card = show({ valid: true, entries_checked: 3 })

    expect(within(card).getByText(/^Verificado el .*2026/)).toBeInTheDocument()
  })

  it('follows the language switcher', async () => {
    await i18n.changeLanguage('en')
    const card = show({ valid: false, entries_checked: 5, first_broken_seq: 5, reason: 'sequence gap or duplicate' })

    expect(card).toHaveAccessibleName(en.audit.verify.broken.title)
    expect(within(card).getByText(en.audit.verify.reason.sequence)).toBeInTheDocument()
  })
})
