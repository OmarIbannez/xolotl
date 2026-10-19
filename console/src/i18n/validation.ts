import esMX from './es-MX.json'

export type ValidationKey = keyof typeof esMX.validation

const VALIDATION_KEYS: ReadonlySet<string> = new Set(Object.keys(esMX.validation))

export function isValidationKey(value: string): value is ValidationKey {
  return VALIDATION_KEYS.has(value)
}

/**
 * Names a `validation.*` message for a form schema, for example
 * `z.string().min(1, msgKey('required'))`. The schema carries the key and `Field`
 * translates it when it renders, so messages follow the language switcher and the
 * key is checked against the catalogue.
 */
export function msgKey(key: ValidationKey): string {
  return key
}
