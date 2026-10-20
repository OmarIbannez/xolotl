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
export type ContactInput = Schemas['ContactInput']
export type ContactKind = Schemas['ContactKind']
export type AddressInput = Schemas['AddressInput']
/** An address's street lines: personal data, sent on create and shown only by the reveal. */
export type AddressLines = Schemas['AddressLines']
export type AddressType = Schemas['AddressType']
export type PartyRelationshipInput = Schemas['PartyRelationshipInput']
export type RelationshipType = Schemas['RelationshipType']
export type PepStatus = Schemas['PepStatus']
export type PersonAttributes = Schemas['PersonAttributes']
export type OrganizationAttributes = Schemas['OrganizationAttributes']
export type DocumentResponse = Schemas['DocumentResponse']
export type DocumentType = Schemas['DocumentType']
export type CustomerRelationshipResponse = Schemas['CustomerRelationshipResponse']
export type CustomerRelationshipStatus = Schemas['CustomerRelationshipStatus']
export type RelationshipOpenRequest = Schemas['RelationshipOpenRequest']
export type RelationshipCloseRequest = Schemas['RelationshipCloseRequest']
export type MaskedContact = Schemas['MaskedContact']
export type AddressResponse = Schemas['AddressResponse']
export type PartyRelationshipResponse = Schemas['PartyRelationshipResponse']

export type ListOverview = Schemas['ListOverview']
export type ListOverviewResponse = Schemas['ListOverviewResponse']
export type ListVersionResponse = Schemas['ListVersionResponse']
export type ListVersionPage = Schemas['Page_ListVersionResponse_']
export type DeltaRowResponse = Schemas['DeltaRowResponse']
export type DeltaPage = Schemas['Page_DeltaRowResponse_']
/** One list entry as the library serializes it (`ListEntry.canonical_json`): public list data. */
export type ListEntryResponse = Schemas['ListEntryResponse']
export type ListKind = Schemas['ListKind']
export type ListRequirement = Schemas['ListRequirement']
export type DeltaChange = Schemas['DeltaChange']
export type VersionStatus = Schemas['VersionStatus']
export type EntityType = Schemas['EntityType']

export type PrivateListCreate = Schemas['PrivateListCreate']
export type PrivateListResponse = Schemas['PrivateListResponse']
export type PrivateListVersionResponse = Schemas['PrivateListVersionResponse']
/** One private-list entry, decrypted: personal data, shown only behind an audited reveal. */
export type PrivateListEntryResponse = Schemas['PrivateListEntryResponse']

/** One finding of the screening queue (staff only). */
export type MatchSummary = Schemas['MatchSummary']
/** A finding's staff detail: the subject masked, the explanations, and the current user's actions. */
export type MatchDetail = Schemas['MatchDetail']
/** The plaintext of `GET /v1/screening/matches/{id}/reveal`: audited as `VIEW_PII`. Never cache it. */
export type MatchReveal = Schemas['MatchReveal']
export type HitView = Schemas['HitView']
export type MaskedSubject = Schemas['MaskedSubject']
export type HitHistoryItem = Schemas['HitHistoryItem']
export type DispositionView = Schemas['DispositionView']
export type DispositionRequest = Schemas['DispositionRequest']
export type DispositionResponse = Schemas['DispositionResponse']
export type MatchStatus = Schemas['MatchStatus']
export type HitBand = Schemas['HitBand']
export type Decision = Schemas['Decision']
export type BlockedReason = Schemas['BlockedReason']
export type Page_MatchSummary_ = Schemas['Page_MatchSummary_']

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
  /** Why a refused screening disposition was refused (`DISPOSITION_NOT_ALLOWED` only). */
  blocked_reasons?: string[]
}
