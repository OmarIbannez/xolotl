import type { components } from './schema'

type Schemas = components['schemas']

// ---------------------------------------------------------------------------
// Aliases of the OpenAPI schemas in ./schema.d.ts (regenerate with `npm run gen:api`).
// ---------------------------------------------------------------------------

export type Role = Schemas['Role']
export type Me = Schemas['MeResponse']
export type Tenant = Schemas['TenantResponse']
export type TenantEnv = Schemas['TenantEnv']
export type TenantStatus = Schemas['TenantStatus']
export type LoginRequest = Schemas['LoginRequest']
export type TokenResponse = Schemas['TokenResponse']
export type MfaRequired = Schemas['MfaRequired']
export type TotpLoginRequest = Schemas['TotpLoginRequest']
export type PasswordChangeRequest = Schemas['PasswordChangeRequest']

export type ApiKeyResponse = Schemas['ApiKeyResponse']
export type ApiKeyStatus = Schemas['ApiKeyStatus']
export type ApiKeyPage = Schemas['Page_ApiKeyResponse_']
export type ApiKeyCreateRequest = Schemas['ApiKeyCreateRequest']
/** Returned once by `POST /v1/api-keys`: `key` is the full secret and is never shown again. */
export type ApiKeyCreatedResponse = Schemas['ApiKeyCreatedResponse']

export type StaffResponse = Schemas['StaffResponse']
export type StaffStatus = Schemas['StaffStatus']
export type StaffPage = Schemas['Page_StaffResponse_']
export type StaffCreateRequest = Schemas['StaffCreateRequest']
/** Returned once by `POST /v1/staff`: `initial_password` is never shown again. */
export type StaffCreatedResponse = Schemas['StaffCreatedResponse']

export type AuditEventResponse = Schemas['AuditEventResponse']
export type AuditPage = Schemas['Page_AuditEventResponse_']
export type ChainVerification = Schemas['ChainVerification']

export type ConnectorResponse = Schemas['ConnectorResponse']
export type ConnectorList = Schemas['ConnectorListResponse']
export type ConnectorUsage = Schemas['ConnectorUsageResponse']
export type ConnectorUsageRow = Schemas['ConnectorUsageRow']
export type ConnectorMode = Schemas['ConnectorMode']
export type CheckType = Schemas['CheckType']
export type ConnectorRoute = Schemas['ConnectorRoute']
export type CircuitState = Schemas['CircuitState']

// ---------------------------------------------------------------------------
// Errors: every non-2xx answer is `application/problem+json` (spec section 7).
// The OpenAPI document does not describe it, so it is typed here.
// ---------------------------------------------------------------------------

export interface ProblemFieldError {
  loc: (string | number)[]
  msg: string
  type: string
}

export interface ProblemDetails {
  type: string
  title: string
  status: number
  detail: string
  instance: string
  /** Stable UPPER_SNAKE code, for example `UNAUTHENTICATED` or `ROLE_FORBIDDEN`. */
  code: string
  errors?: ProblemFieldError[]
}
