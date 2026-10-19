import { createHmac } from 'node:crypto'

const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'

/** RFC 4648 base32 (what authenticator apps and `pyotp` use for a TOTP secret). */
export function base32Decode(text: string): Buffer {
  const bytes: number[] = []
  let buffer = 0
  let bits = 0
  for (const char of text.replace(/=+$/, '').toUpperCase()) {
    const index = BASE32.indexOf(char)
    if (index < 0) throw new Error(`not a base32 character: ${char}`)
    buffer = (buffer << 5) | index
    bits += 5
    if (bits >= 8) {
      bits -= 8
      bytes.push((buffer >> bits) & 0xff)
      buffer &= (1 << bits) - 1
    }
  }
  return Buffer.from(bytes)
}

/** The length of one TOTP step, in milliseconds. */
export const TOTP_STEP_MS = 30_000

/**
 * The code an authenticator app shows for `secret` at `atMs` (RFC 6238 over HMAC-SHA1, a
 * 30 second step and 6 digits, which is what the API's `pyotp.TOTP(secret)` uses).
 */
export function totp(secret: string, atMs: number = Date.now(), digits = 6): string {
  const step = Math.floor(atMs / TOTP_STEP_MS)
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(step))
  const hmac = createHmac('sha1', base32Decode(secret)).update(counter).digest()
  const offset = (hmac[hmac.length - 1] ?? 0) & 0x0f
  const binary = hmac.readUInt32BE(offset) & 0x7fffffff
  return String(binary % 10 ** digits).padStart(digits, '0')
}

/**
 * The code of the step after the one `atMs` falls in. A code that has been accepted once
 * is spent for a verifier that refuses a replay (RFC 6238 section 5.2), so a second login
 * right after the first needs the next step's code; the API accepts the step before, the
 * current one and the next (`valid_window=1`), so this works whether or not it checks.
 */
export function nextTotp(secret: string, atMs: number = Date.now()): string {
  return totp(secret, atMs + TOTP_STEP_MS)
}
