/** Minimum length the API enforces for a new password (spec section 6.1). */
export const MIN_PASSWORD_LENGTH = 12

export type PasswordStrength = 'empty' | 'weak' | 'fair' | 'strong'

/** Length in characters as the API counts them (code points, not UTF-16 units). */
export function passwordLength(password: string): number {
  return [...password].length
}

/**
 * A hint for the person typing, not a gate: the API only requires 12 characters and
 * a password different from the current one.
 */
export function passwordStrength(password: string): PasswordStrength {
  const length = passwordLength(password)
  if (length === 0) return 'empty'
  if (length < MIN_PASSWORD_LENGTH) return 'weak'
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length
  return (length >= 16 && classes >= 3) || length >= 24 ? 'strong' : 'fair'
}
