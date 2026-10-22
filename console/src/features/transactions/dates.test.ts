import { describe, expect, it } from 'vitest'
import { mexicoCityToday } from './dates'

describe('mexicoCityToday', () => {
  it('names the Mexico City day, not the UTC one', () => {
    // 03:00 UTC on 8 December is still 7 December in Mexico City (UTC-6).
    expect(mexicoCityToday(new Date('2026-12-08T03:00:00Z'))).toBe('2026-12-07')
    expect(mexicoCityToday(new Date('2026-12-08T07:00:00Z'))).toBe('2026-12-08')
  })
})
