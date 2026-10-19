import { describe, expect, it } from 'vitest'
import { showInvisibles } from './safe-text'

// Built with fromCodePoint on purpose: the characters under test must never sit in this file.
const char = (code: number) => String.fromCodePoint(code)
/** How a code point is shown: a backslash, "u", then its hex digits. */
const shown = (hex: string) => `\\u${hex}`

describe('showInvisibles', () => {
  it('leaves ordinary text alone, accents, emoji and other scripts included', () => {
    for (const text of ['auth.login_failed', 'Ñandú ñ', 'José Pérez', '日本語', 'a 😀']) {
      expect(showInvisibles(text)).toBe(text)
    }
  })

  it('shows a bidirectional override as a visible escape, so a value cannot disguise itself', () => {
    expect(showInvisibles(`invoice${char(0x202e)}gnp.exe`)).toBe(`invoice${shown('202e')}gnp.exe`)
    expect(showInvisibles(`${char(0x2066)}x${char(0x2069)}`)).toBe(`${shown('2066')}x${shown('2069')}`)
  })

  it('escapes zero-width characters, controls, line separators and the byte order mark', () => {
    expect(showInvisibles(`a${char(0x200b)}b`)).toBe(`a${shown('200b')}b`)
    expect(showInvisibles(`a${char(0x200d)}b`)).toBe(`a${shown('200d')}b`)
    expect(showInvisibles(`a${char(0xfeff)}b`)).toBe(`a${shown('feff')}b`)
    expect(showInvisibles(`a${char(0x85)}b`)).toBe(`a${shown('0085')}b`)
    expect(showInvisibles(`a${char(0x2028)}b${char(0x2029)}c`)).toBe(`a${shown('2028')}b${shown('2029')}c`)
    expect(showInvisibles('tab\there')).toBe(`tab${shown('0009')}here`)
  })

  it('can leave line feeds alone, for indented JSON whose line breaks are layout', () => {
    expect(showInvisibles('{\n  "a": 1\n}', { keepLineFeeds: true })).toBe('{\n  "a": 1\n}')
    expect(showInvisibles('{\n  "a": 1\n}')).toBe(`{${shown('000a')}  "a": 1${shown('000a')}}`)
    // Only line feeds: other controls and direction overrides are still shown.
    expect(showInvisibles(`a\n${char(0x202e)}`, { keepLineFeeds: true })).toBe(`a\n${shown('202e')}`)
  })

  it('escapes characters outside the basic plane with their full code point', () => {
    // U+E0041 is a "tag" character: invisible.
    expect(showInvisibles(`a${char(0xe0041)}b`)).toBe('a\\u{e0041}b')
  })
})
