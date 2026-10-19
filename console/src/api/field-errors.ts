import type { ValidationKey } from '@/i18n/validation'
import { isApiError } from './errors'

export interface FieldProblem<TField extends string> {
  field: TField
  /** A `validation.*` key, which `Field` translates. */
  message: ValidationKey
}

/** What a pydantic error type means, in the words of the `validation.*` catalogue. */
function messageFor(type: string): ValidationKey {
  switch (type) {
    case 'missing':
    case 'string_too_short':
    case 'too_short':
      return 'required'
    case 'string_too_long':
    case 'too_long':
      return 'tooLong'
    default:
      return 'invalid'
  }
}

/**
 * The per-field problems of a 422 `VALIDATION_FAILED` answer (its `errors[]`, with `loc` such
 * as `['body', 'name']`), for the fields this form has. The server's English `msg` is never
 * shown: each problem becomes a catalogue key by its type, so it follows the language switcher.
 * An item of a list (`['body', 'scopes', 2]`) counts as a problem of the list. Anything else,
 * including a bare 422 with no `errors[]`, finds nothing, and the form shows the failure as a whole.
 */
export function fieldProblems<TField extends string>(
  error: unknown,
  fields: readonly TField[],
): FieldProblem<TField>[] {
  if (!isApiError(error) || error.code !== 'VALIDATION_FAILED') return []
  const problems: FieldProblem<TField>[] = []
  for (const { loc, type } of error.errors) {
    const [where, name] = loc
    const field = where === 'body' ? fields.find((candidate) => candidate === name) : undefined
    if (field !== undefined && !problems.some((problem) => problem.field === field)) {
      problems.push({ field, message: messageFor(type) })
    }
  }
  return problems
}
