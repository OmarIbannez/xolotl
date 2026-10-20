import { describe, expect, it } from 'vitest'
import { overview } from './fixtures'
import { errorCodeOf, freshnessOf } from './freshness'

describe('freshnessOf', () => {
  it('is EMPTY while the list has no active version, whatever else is true', () => {
    expect(freshnessOf(overview({ active_seq: null, stale: true, failing: true }))).toBe('EMPTY')
  })

  it('is FAILING when the last fetch failed, even if the version is also stale', () => {
    expect(freshnessOf(overview({ failing: true, stale: true, last_error: 'HTTP_503' }))).toBe('FAILING')
  })

  it('is STALE when the version is older than twice the poll interval', () => {
    expect(freshnessOf(overview({ stale: true }))).toBe('STALE')
  })

  it('is FRESH otherwise', () => {
    expect(freshnessOf(overview())).toBe('FRESH')
  })
})

describe('errorCodeOf', () => {
  it('keeps the code before the reason', () => {
    expect(errorCodeOf('LIST_MASS_REMOVAL: removed 412 of 1003')).toBe('LIST_MASS_REMOVAL')
    expect(errorCodeOf('HTTP_503')).toBe('HTTP_503')
  })

  it('has nothing for no error', () => {
    expect(errorCodeOf(null)).toBeNull()
    expect(errorCodeOf('  ')).toBeNull()
  })
})
