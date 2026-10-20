// @vitest-environment jsdom
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { renderWithProviders } from '@/test/render'
import { MATCH_KINDS, QUALIFIER_ATTRIBUTES, QUALIFIER_OUTCOMES, RULE_CODES } from './explanation-codes'
import { ExplanationTable } from './ExplanationTable'
import { features, hit } from './fixtures'

const text = esMX.screening.match

describe('ExplanationTable', () => {
  it('every match kind and qualifier outcome has a label', () => {
    const everything = hit({
      features: features({
        alignment: MATCH_KINDS.map((kind, index) => ({
          query: `q${index}`,
          entry: `e${index}`,
          kind,
          similarity: 0.9,
          weight: 0.2,
          role: 'GIVEN',
        })),
        qualifiers: QUALIFIER_OUTCOMES.map((outcome, index) => ({
          attribute: QUALIFIER_ATTRIBUTES[index],
          outcome,
          adjustment: 0,
          detail: 'x',
        })),
        rules: RULE_CODES.map((code) => ({ code, detail: 'x' })),
      }),
    })

    renderWithProviders(<ExplanationTable hit={everything} />)

    for (const kind of MATCH_KINDS) {
      expect(text.explanation.kinds[kind], kind).toBeTruthy()
      expect(screen.getByText(text.explanation.kinds[kind])).toBeTruthy()
    }
    for (const outcome of QUALIFIER_OUTCOMES) {
      expect(text.qualifiers.outcomes[outcome], outcome).toBeTruthy()
      expect(screen.getByText(text.qualifiers.outcomes[outcome])).toBeTruthy()
    }
    for (const attribute of QUALIFIER_ATTRIBUTES) {
      expect(text.qualifiers.attributes[attribute], attribute).toBeTruthy()
    }
    for (const code of RULE_CODES) {
      expect(screen.getByText(text.rules.codes[code])).toBeTruthy()
    }
    // The codes themselves are never shown.
    const page = document.body.textContent ?? ''
    for (const code of [...MATCH_KINDS, ...QUALIFIER_OUTCOMES, ...RULE_CODES]) {
      expect(page).not.toContain(code)
    }
  })

  it('says so when nothing lined up, no qualifier ran and no rule applied', () => {
    renderWithProviders(
      <ExplanationTable hit={hit({ features: features({ alignment: [], qualifiers: [], rules: [], unmatched_query: [] }) })} />,
    )

    expect(screen.getByText(text.explanation.empty)).toBeTruthy()
    expect(screen.getByText(text.qualifiers.empty)).toBeTruthy()
    expect(screen.getByText(text.rules.empty)).toBeTruthy()
  })
})
