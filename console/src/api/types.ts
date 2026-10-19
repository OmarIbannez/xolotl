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

export type EffectivePolicyResponse = Schemas['EffectivePolicyResponse']
export type EffectivePolicy = Schemas['EffectivePolicy']
export type ConversionResponse = Schemas['ConversionResponse']
export type PackVersionPage = Schemas['Page_PackVersionResponse_']
export type AssignmentPage = Schemas['Page_AssignmentResponse_']
export type OverridePage = Schemas['Page_OverrideResponse_']
export type UnitsResponse = Schemas['UnitsResponse']
export type UnitLookupResponse = Schemas['UnitLookupResponse']
export type UnitValuesResponse = Schemas['UnitValuesResponse']
export type HolidaysResponse = Schemas['HolidaysResponse']
export type DueResponse = Schemas['DueResponse']
export type DeadlineRule = Schemas['DeadlineRule']
export type DeadlineKind = Schemas['DeadlineKind']
export type ConversionRule = Schemas['ConversionRule']
export type HolidayScope = Schemas['HolidayScope']
/** An amount in a unit as the API answers it: `amount` is a decimal string, never a float. */
export type Quantity = Schemas['Quantity-Output']
export type Confidence = Schemas['Confidence']

export type PartyResponse = Schemas['PartyResponse']
/** The staff shape of `GET /v1/parties/{id}`: masked values, with the sub-collections embedded. */
export type PartyStaffResponse = Schemas['PartyStaffResponse']
/** The plaintext of `GET /v1/parties/{id}/pii`: each request is audited as `VIEW_PII`. Never cache it. */
export type PartyPiiResponse = Schemas['PartyPiiResponse']
export type PartyCreateRequest = Schemas['PartyCreateRequest']
export type PartyUpdateRequest = Schemas['PartyUpdateRequest']
export type PartyPage = Schemas['Page_PartyResponse_']
export type PartyType = Schemas['PartyType']
export type PartyStatus = Schemas['PartyStatus']
export type IdentifierType = Schemas['IdentifierType']
export type MaskedIdentifier = Schemas['MaskedIdentifier']
export type LegalHoldRequest = Schemas['LegalHoldRequest']
export type PersonInput = Schemas['PersonInput']
export type OrganizationInput = Schemas['OrganizationInput']
export type IdentifierInput = Schemas['IdentifierInput']

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
