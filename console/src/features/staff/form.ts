import { z } from 'zod'
import type { Role, StaffCreateRequest } from '@/api/types'
import { msgKey } from '@/i18n/validation'

/**
 * Every role the API defines, from least to most privileged. Typed as a `Record<Role, ...>`
 * so the build fails when the API adds a role that is missing here.
 */
const RANK: Record<Role, number> = {
  ANALYST: 0,
  SENIOR_ANALYST: 1,
  MLRO: 2,
  AUDITOR_RO: 3,
  ADMIN: 4,
}

export const ROLES: readonly Role[] = (Object.keys(RANK) as Role[]).sort((a, b) => RANK[a] - RANK[b])

function isRole(value: unknown): value is Role {
  return typeof value === 'string' && Object.hasOwn(RANK, value)
}

export const staffSchema = z.object({
  // The same check as the sign-in form, so any account created here can sign in here.
  email: z.string().trim().min(1, msgKey('required')).pipe(z.email(msgKey('email'))),
  displayName: z.string().trim().min(1, msgKey('required')).max(200, msgKey('displayNameTooLong')),
  roles: z.array(z.custom<Role>(isRole)).min(1, msgKey('rolesRequired')),
})

export type StaffFormValues = z.input<typeof staffSchema>

export function toStaffRequest(values: z.output<typeof staffSchema>): StaffCreateRequest {
  return { email: values.email, display_name: values.displayName, roles: values.roles }
}

/** The API's name for each form field, to point a server-side validation error at its input. */
export const FORM_FIELD_OF = {
  email: 'email',
  display_name: 'displayName',
  roles: 'roles',
} as const
