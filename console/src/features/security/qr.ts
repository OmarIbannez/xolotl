/**
 * How the enrolment QR code is drawn. Dark modules on a white square whatever the theme,
 * with the four-module quiet zone the QR specification asks for: scanners rely on that
 * contrast, so these two colours are deliberately not theme tokens.
 */
export const QR = {
  size: 176,
  level: 'M',
  marginSize: 4,
  bgColor: '#ffffff',
  fgColor: '#000000',
} as const

/** A base32 secret in groups of four, which is how people read it into an app. */
export function groupSecret(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, '$1 ')
}
