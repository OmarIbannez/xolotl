import { describe, expect, it } from 'vitest'

describe('the test time zone', () => {
  it('is pinned in vite.config.ts, so an expected date or hour does not depend on the machine', () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('America/Mexico_City')
    expect(new Date('2026-10-18T16:24:05Z').getHours()).toBe(10) // UTC-6 all year since 2022
  })
})
