import { describe, expect, it } from 'vitest'
import { MIN_PASSWORD_LENGTH, passwordLength, passwordStrength } from './password-strength'

describe('passwordStrength', () => {
  it('is empty until something is typed', () => {
    expect(passwordStrength('')).toBe('empty')
  })

  it('is weak below the 12-character minimum the API enforces', () => {
    expect(passwordStrength('a')).toBe('weak')
    expect(passwordStrength('Abcdef1!xyz')).toBe('weak') // 11 characters
  })

  it('is fair from 12 characters', () => {
    expect(MIN_PASSWORD_LENGTH).toBe(12)
    expect(passwordStrength('abcdefghijkl')).toBe('fair')
  })

  it('is strong when long and varied, or very long', () => {
    expect(passwordStrength('Long-Mixed-Sample-Phrase-1')).toBe('strong')
    expect(passwordStrength('a'.repeat(24))).toBe('strong')
    expect(passwordStrength('abcdefghijklmnop')).toBe('fair') // 16 characters, one class
  })
})

describe('passwordLength', () => {
  it('counts characters the way the API does (code points), not UTF-16 units', () => {
    expect(passwordLength('abc')).toBe(3)
    expect(passwordLength('😀😀😀😀😀😀')).toBe(6) // 12 UTF-16 units, 6 characters
    expect(passwordStrength('😀😀😀😀😀😀')).toBe('weak')
  })
})
