/**
 * The codes a hit's stored explanation carries (the screening library's `MatchKind`,
 * `QualifierOutcome`, qualifier attributes and rule codes). Each has a `screening.match.*`
 * catalogue key: the console translates them and never shows one raw.
 */
export const MATCH_KINDS = ['EXACT', 'PHONETIC', 'NICKNAME', 'INITIAL', 'FUZZY'] as const
export const QUALIFIER_OUTCOMES = ['MATCH', 'PARTIAL', 'CONFLICT', 'ABSENT'] as const
export const QUALIFIER_ATTRIBUTES = ['BIRTH_DATE', 'NATIONALITY', 'COUNTRY', 'GENDER', 'ENTITY_TYPE', 'IDENTIFIER'] as const
export const RULE_CODES = ['ID_MATCH_FLOOR', 'ID_CONFLICT_CAP', 'IDENTIFIER_ONLY_LIST', 'ENTITY_TYPE_MISMATCH'] as const
