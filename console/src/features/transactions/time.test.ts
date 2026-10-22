import { describe, expect, it } from 'vitest'
import { MEXICO_CITY, mexicoCityDate, mexicoCityFormat, mexicoCityTime } from './time'

describe('Mexico City time', () => {
  it('formats in America/Mexico_City whatever the zone of the browser', () => {
    expect(MEXICO_CITY).toBe('America/Mexico_City')
    expect(mexicoCityFormat('es-MX', { dateStyle: 'medium' }).resolvedOptions().timeZone).toBe('America/Mexico_City')
    expect(mexicoCityFormat('en', { timeStyle: 'short' }).resolvedOptions().timeZone).toBe('America/Mexico_City')
  })

  it('puts an instant just after midnight UTC on the previous Mexico City day', () => {
    expect(mexicoCityDate('2026-10-21T02:00:00Z', 'en')).toBe('Oct 20, 2026')
    expect(mexicoCityTime('2026-10-21T02:00:00Z', 'en')).toMatch(/^8:00\sPM$/)
  })
})
