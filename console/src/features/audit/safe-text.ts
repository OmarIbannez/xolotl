/**
 * Zero-width characters, bidirectional overrides, line separators and other controls.
 * They draw nothing (or rearrange what is drawn), so a value containing them can read as
 * something it is not.
 */
const INVISIBLE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu

interface ShowInvisiblesOptions {
  /**
   * Leave line feeds alone. Only for text whose line breaks are layout, such as indented
   * JSON: JSON writes a line feed inside a string as a two-character escape, so a raw one
   * there cannot have come from the data.
   */
  keepLineFeeds?: boolean
}

/**
 * Make a value from the audit trail safe to read. React already keeps data from becoming
 * markup; this keeps it from disguising itself. Audit values are written by many hands
 * (a staff member's name, an API key's name, request fields) and an audit trail exists to
 * tell what really happened, so invisible and direction-changing characters are shown as
 * visible escapes (a right-to-left override, U+202E, reads as backslash, u, 202e) instead
 * of being drawn.
 */
export function showInvisibles(text: string, { keepLineFeeds = false }: ShowInvisiblesOptions = {}): string {
  return text.replace(INVISIBLE, (char) => {
    if (keepLineFeeds && char === '\n') return char
    const code = char.codePointAt(0) ?? 0
    return code > 0xffff ? `\\u{${code.toString(16)}}` : `\\u${code.toString(16).padStart(4, '0')}`
  })
}
