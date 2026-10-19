import { describe, expect, it } from 'vitest'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { isKnownReason, reasonKey } from './verify-reason'

describe('reasonKey', () => {
  it('maps each reason the API gives to its own message', () => {
    expect(reasonKey('sequence gap or duplicate')).toBe('audit.verify.reason.sequence')
    expect(reasonKey('prev_hash does not match the previous entry')).toBe('audit.verify.reason.prevHash')
    expect(reasonKey('content does not match its hash')).toBe('audit.verify.reason.content')
  })

  it('has a generic message for a reason it does not know, or none', () => {
    expect(reasonKey('some future reason')).toBe('audit.verify.reason.unknown')
    expect(reasonKey('')).toBe('audit.verify.reason.unknown')
    expect(reasonKey(null)).toBe('audit.verify.reason.unknown')
    expect(reasonKey(undefined)).toBe('audit.verify.reason.unknown')
    expect(reasonKey('constructor')).toBe('audit.verify.reason.unknown') // not a lookup on Object.prototype
  })

  it('names messages that exist in both languages', () => {
    const keys = [
      reasonKey('sequence gap or duplicate'),
      reasonKey('prev_hash does not match the previous entry'),
      reasonKey('content does not match its hash'),
      reasonKey('whatever'),
    ]
    for (const key of keys) {
      const name = key.slice('audit.verify.reason.'.length) as keyof typeof esMX.audit.verify.reason
      expect(esMX.audit.verify.reason[name]).toBeTruthy()
      expect(en.audit.verify.reason[name]).toBeTruthy()
    }
  })
})

describe('isKnownReason', () => {
  it('tells the reasons the console can translate from those it cannot', () => {
    expect(isKnownReason('content does not match its hash')).toBe(true)
    expect(isKnownReason('something else')).toBe(false)
    expect(isKnownReason(null)).toBe(false)
  })
})
