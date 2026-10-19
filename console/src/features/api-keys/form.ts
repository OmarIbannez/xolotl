import { z } from 'zod'
import type { ApiKeyCreateRequest, TenantEnv } from '@/api/types'
import { msgKey } from '@/i18n/validation'

/** The API's grammar for a scope: `<area>:<read|write>`, lowercase with underscores. */
export const SCOPE_PATTERN = /^[a-z][a-z_]*:(read|write)$/

/**
 * Scopes offered as checkboxes: only ones some API route honours (`audit:read` is not offered
 * because none does). Anything else that fits the grammar can be typed in.
 */
export const PRESET_SCOPES = [
  { scope: 'tenants:read', labelKey: 'apiKeys.scopes.tenantsRead' },
  { scope: 'tenants:write', labelKey: 'apiKeys.scopes.tenantsWrite' },
] as const

export function isValidScope(scope: string): boolean {
  return SCOPE_PATTERN.test(scope)
}

export const apiKeySchema = z.object({
  name: z.string().trim().min(1, msgKey('required')).max(120, msgKey('nameTooLong')),
  scopes: z.array(z.string().regex(SCOPE_PATTERN, msgKey('scopeFormat'))).min(1, msgKey('scopesRequired')),
  /**
   * What is typed in the "another scope" box and not added yet. It is part of the form so
   * that a scope typed but never added blocks the submit instead of being dropped silently.
   */
  scopeDraft: z.string().trim().max(0, msgKey('scopeDraftPending')),
})

export type ApiKeyFormValues = z.input<typeof apiKeySchema>

/** The request for a valid form. The key's environment is always the tenant's. */
export function toApiKeyRequest(
  values: z.output<typeof apiKeySchema>,
  env: TenantEnv,
): ApiKeyCreateRequest {
  return { name: values.name, scopes: values.scopes, env }
}
