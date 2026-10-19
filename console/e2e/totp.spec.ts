import { expect, test } from '@playwright/test'
import { base32Decode, nextTotp, TOTP_STEP_MS, totp } from './totp'

// RFC 6238, appendix B: the SHA-1 secret is the ASCII string "12345678901234567890". The
// RFC prints 8-digit codes; the 6-digit codes are the last six of those.
const RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'

test.describe('the TOTP helper the smoke signs in with', () => {
  test('decodes the RFC secret', () => {
    expect(base32Decode(RFC_SECRET).toString('ascii')).toBe('12345678901234567890')
  })

  test('decodes lower case and padding', () => {
    expect(base32Decode('mzxw6===').toString('ascii')).toBe('foo')
  })

  test('refuses text that is not base32', () => {
    expect(() => base32Decode('NOT-BASE32')).toThrow('not a base32 character')
  })

  for (const [seconds, eight] of [
    [59, '94287082'],
    [1_111_111_109, '07081804'],
    [1_111_111_111, '14050471'],
    [1_234_567_890, '89005924'],
    [2_000_000_000, '69279037'],
    [20_000_000_000, '65353130'],
  ] as const) {
    test(`matches the RFC vector at ${seconds} s`, () => {
      expect(totp(RFC_SECRET, seconds * 1000, 8)).toBe(eight)
      expect(totp(RFC_SECRET, seconds * 1000)).toBe(eight.slice(2))
    })
  }

  test('changes every 30 seconds and not before', () => {
    expect(totp(RFC_SECRET, 30_000)).toBe(totp(RFC_SECRET, 59_999))
    expect(totp(RFC_SECRET, 30_000)).not.toBe(totp(RFC_SECRET, 60_000))
  })

  test('the next step is the code of the following 30 seconds', () => {
    // Anywhere in a step, including its first and last millisecond.
    for (const at of [0, 1, 29_999, 30_000, 1_111_111_111_000, 1_111_111_139_999]) {
      expect(nextTotp(RFC_SECRET, at)).toBe(totp(RFC_SECRET, at + TOTP_STEP_MS))
    }
  })

  test('the next step is never the code that was just spent', () => {
    // What a login after turning TOTP on relies on: a verifier that refuses to see a code
    // twice (RFC 6238 section 5.2) refuses the current step, not the next one.
    for (let step = 0; step < 1000; step++) {
      const at = step * TOTP_STEP_MS
      expect(nextTotp(RFC_SECRET, at)).not.toBe(totp(RFC_SECRET, at))
    }
  })
})
