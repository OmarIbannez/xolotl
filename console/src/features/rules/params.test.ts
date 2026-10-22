import { describe, expect, it } from 'vitest'
import type { ParamView } from '@/api/types'
import { lastSixMonths, parseDecimal, segmentOf, splitCodes, valueOf } from './params'

function param(type: ParamView['type']): ParamView {
  return { name: 'p', value: '1', type, min: null, max: null, choices: null, tighten: null, unit: null, source: 'template X v1' }
}

describe('parseDecimal', () => {
  it('keeps the digits, dropping only thousands grouping', () => {
    expect(parseDecimal('5000')).toBe('5000')
    expect(parseDecimal(' 0.70 ')).toBe('0.70')
    expect(parseDecimal('5,000.50')).toBe('5000.50')
    expect(parseDecimal('12345678901234567890.123456789')).toBe('12345678901234567890.123456789')
  })

  it('refuses a decimal comma, a sign and anything that is not a number', () => {
    for (const text of ['0,70', '0,700', '0,700.5', '01,000', '5000,5', '-1', '+1', '1e3', '', '.5', '5.', '1,00']) {
      expect(parseDecimal(text), text).toBeNull()
    }
  })
})

describe('valueOf', () => {
  it('sends a decimal as a string and an integer as a number, never a float', () => {
    expect(valueOf(param('DECIMAL'), '0.75')).toBe('0.75')
    expect(valueOf(param('INT'), '3')).toBe(3)
    expect(valueOf(param('INT'), '3.5')).toBeNull()
    expect(valueOf(param('STR'), ' P7D ')).toBe('P7D')
    expect(valueOf(param('STR'), ' ')).toBeNull()
    expect(valueOf(param('BOOL'), false)).toBe(false)
  })
})

describe('the segment', () => {
  it('reads codes separated by commas or spaces, upper-cased and without repeats', () => {
    expect(splitCodes('n1, N2 n1,,')).toEqual(['N1', 'N2'])
  })

  it('sends three empty lists when every list is empty: null would keep the stored segment', () => {
    expect(segmentOf({ tier: ' ', risk_rating: '', monitoring_profile: '' })).toEqual({
      tier: [],
      risk_rating: [],
      monitoring_profile: [],
    })
    expect(segmentOf({ tier: 'n1', risk_rating: 'high', monitoring_profile: '' })).toEqual({
      tier: ['N1'],
      risk_rating: ['HIGH'],
      monitoring_profile: [],
    })
  })
})

describe('lastSixMonths', () => {
  it('ends today and starts the day after the same day six months earlier', () => {
    expect(lastSixMonths('2026-10-22')).toEqual({ range_start: '2026-04-23', range_end: '2026-10-22' })
    expect(lastSixMonths('2026-01-15')).toEqual({ range_start: '2025-07-16', range_end: '2026-01-15' })
  })

  it('clamps to the end of a shorter month', () => {
    expect(lastSixMonths('2026-08-31')).toEqual({ range_start: '2026-03-01', range_end: '2026-08-31' })
  })
})
