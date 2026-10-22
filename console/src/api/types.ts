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
/** The matcher's evaluation report shipped with the build: figures per list kind and floors. */
export type EvaluationReport = Schemas['EvaluationReportResponse']
export type EvaluationKind = Schemas['ListKindMetrics']
export type EvaluationFloor = Schemas['Floor']

/** A synchronous check of one party (`party_id`) or one inline subject (`subject`), staff may narrow `lists`. */
export type CheckRequest = Schemas['CheckRequest']
/** The subject of an ad hoc check: personal data, never kept in a query key, the URL or storage. */
export type AdHocSubjectInput = Schemas['AdHocSubjectInput']
export type AdHocIdentifier = Schemas['AdHocIdentifier']
/** What an API client gets back from a check: the status only. */
export type CheckResponse = Schemas['CheckResponse']
/** What staff get back from a check: the status and every hit with its explanation. */
export type CheckStaffResponse = Schemas['CheckStaffResponse']
/** A party's screening status as an API client sees it (`status: null` when never screened). */
export type PartyScreeningResponse = Schemas['PartyScreeningResponse']
/** A party's screening status as staff see it: also the open finding count and the findings. */
export type PartyScreeningStaffResponse = Schemas['PartyScreeningStaffResponse']
export type SubjectStatus = Schemas['SubjectStatus']

/** What an API client sees of a transaction: identity and status only (tipping-off, spec section 15). */
export type TransactionClientView = Schemas['TransactionClientView']
/** The staff shape of a transaction: unit amounts, period keys, the masked counterparty, the snapshot. */
export type TransactionStaffView = Schemas['TransactionStaffView']
/** The staff shape plus `raw` and the counterparty in plaintext: audited as `VIEW_PII`. Never cache it. */
export type TransactionRevealedView = Schemas['TransactionRevealedView']
export type TransactionStatus = Schemas['TransactionStatus']
export type Page_TransactionStaffView_ = Schemas['Page_TransactionStaffView_']
/** One threshold unit of a transaction: `amount` is a decimal string, never a float. */
export type UnitAmountView = Schemas['UnitAmountView']
export type PeriodKeys = Schemas['PeriodKeys']
export type ParkedInfo = Schemas['ParkedInfo']
export type ParkReason = Schemas['ParkReason']
/** A counterparty, masked: which values are on record, never the values themselves. */
export type CounterpartySummary = Schemas['CounterpartySummary']
export type Instrument = Schemas['Instrument']
export type Rail = Schemas['Rail']

// Counterparties (SP-04 plan 4c, C3): masked rows and detail, and the audited reveal.
export type CounterpartyKind = Schemas['CounterpartyKind']
export type CounterpartyScreeningStatus = Schemas['CounterpartyScreeningStatus']
/** A counterparty as the Contrapartes list shows it: initials and last-four tails only. */
export type CounterpartyResponse = Schemas['CounterpartyResponse']
/** A counterparty with its findings (links into the queue) and its last runs. */
export type CounterpartyDetail = Schemas['CounterpartyDetail']
/** A counterparty's values in plaintext; each one was audited as `VIEW_PII`. */
export type RevealedCounterparty = Schemas['RevealedCounterparty']
export type RunResponse = Schemas['RunResponse']
export type FileStatus = Schemas['FileStatus']
/** An uploaded transactions file and its row counters (202 for a new file, 200 for a known one). */
export type FileStatusResponse = Schemas['FileStatusResponse']
/** A transactions batch (the schema name is qualified: screening also has a `BatchResponse`). */
export type TransactionBatchResponse = Schemas['xolotl__modules__monitoring__schemas__BatchResponse']
export type BackfillRequest = Schemas['BackfillRequest']
export type BackfillAccepted = Schemas['BackfillAccepted']

// Rails and behaviour profiles (SP-04 plan 4c, C4 and C8): staff only, never shown to a client.
export type FailMode = Schemas['FailMode']
/** A rail's fail mode; `explicit` is false while the rail follows the deployment's default. */
export type RailSettingResponse = Schemas['RailSettingResponse']
export type RailSettingRequest = Schemas['RailSettingRequest']
/**
 * One month of a relationship's behaviour. `metrics`, `declared_profile` and `deviation` are free
 * JSON in the OpenAPI document: amounts are strings of whole MXN centavos, shares strings with
 * four decimals, ratios strings or null (`features/parties/profile-form.ts` reads them).
 */
export type BehaviourProfileResponse = Schemas['BehaviourProfileResponse']
/** The declared monthly profile a staff writer sets on a customer relationship (MXN only). */
export type DeclaredProfile = Schemas['DeclaredProfile']
/** `{declared_profile: null}` clears the declaration. */
export type RelationshipPatchRequest = Schemas['RelationshipPatchRequest']

// Rule hits (SP-04 plan 4b, B12): staff only, never shown to an API client (tipping-off, spec section 15).
export type Severity = Schemas['Severity']
export type HitOutcome = Schemas['HitOutcome']
export type HitTransactionRole = Schemas['HitTransactionRole']
/** A hit as the queue shows it: the party masked (initials and masked identifiers). */
export type HitSummary = Schemas['HitSummary']
export type Page_HitSummary_ = Schemas['Page_HitSummary_']
/** A hit with its evidence, the rendered sentence and its newest linked transactions (masked). */
export type HitDetail = Schemas['HitDetail']
/** The values a rule read and the parameters it ran with: decimal strings, never floats. */
export type EvidenceView = Schemas['EvidenceView']
export type EvidenceAggregate = Schemas['EvidenceAggregate']
/** A transaction behind a hit, with its role and its counterparty masked. */
export type HitTransactionView = Schemas['HitTransactionView']
export type Page_HitTransactionView_ = Schemas['Page_HitTransactionView_']
/** The hit's party and counterparties in plaintext: audited as `VIEW_PII`. Never cache it. */
export type HitReveal = Schemas['HitReveal']

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
