import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, unwrapReplayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type {
  ActivateRequest,
  Page_RuleResponse_,
  RejectRequest,
  RuleCreateRequest,
  RuleKind,
  RuleResponse,
  RuleVersionStatus,
  SimulateRequest,
  SimulationResponse,
  SimulationStatus,
  TemplateResponse,
  VersionRequest,
} from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'
import { mexicoCityToday } from '@/features/transactions/time'

// Every rule query sits under this root, so a write refreshes the list and the rule at once. A
// rule carries its versions' simulations, whose `sample` and `overlap` say which customers a rule
// would hit: the rule and simulation queries are dropped as soon as no page shows them (gcTime 0).
const RULES = 'rules'

/** How often a running simulation is read again. */
export const SIMULATION_POLL_MS = 5000

const UNFINISHED: readonly SimulationStatus[] = ['QUEUED', 'RUNNING']

/** True while the worker has not finished the simulation. */
export function simulationRunning(status: SimulationStatus | undefined): boolean {
  return status !== undefined && UNFINISHED.includes(status)
}

/**
 * Whether a completed simulation still counts for submission and approval: `valid_until` is the
 * last Mexico City day it does (the API's `MONITORING_SIMULATION_MAX_AGE_DAYS`, 30 by default).
 */
export function simulationValid(simulation: SimulationResponse, today: string = mexicoCityToday()): boolean {
  return simulation.valid_until !== null && simulation.valid_until >= today
}

// `GET /v1/monitoring/rules` reads its filters as one query model, which the OpenAPI document
// describes as a single object parameter: sent as plain `key=value` pairs, as the API reads them.
const FLAT_QUERY = { object: { style: 'form', explode: true } } as const

/** What the tenant's rules are narrowed to; unset keys are left out of the request. */
export interface RuleFilters {
  kind?: RuleKind
  typology?: string
  /** The rules with any version in this status. */
  status?: RuleVersionStatus
}

function compact(filters: RuleFilters): RuleFilters {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)) as RuleFilters
}

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/** The typology library: the latest version of every template, with its sentences. */
export function useTemplates() {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [RULES, tenant, 'templates'],
    queryFn: async () => (await unwrap(api.GET('/monitoring/templates'))) as TemplateResponse[],
    enabled,
  })
}

/** One page of the tenant's rules, oldest first; `cursor` is the previous page's `next_cursor`. */
export function useRules(filters: RuleFilters, cursor?: string) {
  const signedIn = useSignedIn()
  const query = compact(filters)
  return useQuery({
    queryKey: [RULES, signedIn.tenant, 'list', cursor ?? null, query],
    queryFn: async () =>
      (await unwrap(
        api.GET('/monitoring/rules', {
          params: { query: { query, limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
          querySerializer: FLAT_QUERY,
        }),
      )) as Page_RuleResponse_,
    enabled: signedIn.enabled,
    placeholderData: keepPreviousData,
    gcTime: 0,
  })
}

/** One rule with every version, the caller's actions on each and their latest simulations. */
export function useRule(ruleId: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [RULES, tenant, 'one', ruleId],
    queryFn: async () =>
      (await unwrap(api.GET('/monitoring/rules/{rule_id}', { params: { path: { rule_id: ruleId } } }))) as RuleResponse,
    enabled,
    gcTime: 0,
  })
}

/**
 * One simulation, read again every `pollMs` while it is `QUEUED` or `RUNNING`. When a read finds
 * it newly finished, the rules are read again: the version is `SIMULATED` now (or still not).
 */
export function useSimulation(simulationId: string | undefined, { pollMs = SIMULATION_POLL_MS } = {}) {
  const { tenant, enabled } = useSignedIn()
  const qc = useQueryClient()
  const queryKey = [RULES, tenant, 'simulation', simulationId ?? null]
  return useQuery({
    queryKey,
    queryFn: async () => {
      const before = qc.getQueryData<SimulationResponse>(queryKey)?.status
      const simulation = (await unwrap(
        api.GET('/monitoring/simulations/{simulation_id}', {
          params: { path: { simulation_id: simulationId ?? '' } },
        }),
      )) as SimulationResponse
      if (simulationRunning(before) && !simulationRunning(simulation.status)) {
        void qc.invalidateQueries({ queryKey: [RULES, tenant, 'one'] })
        void qc.invalidateQueries({ queryKey: [RULES, tenant, 'list'] })
      }
      return simulation
    },
    enabled: enabled && simulationId !== undefined,
    gcTime: 0,
    refetchInterval: (query) => (simulationRunning(query.state.data?.status) ? pollMs : false),
  })
}

/** After a write: the list and every rule are read again. */
function useInvalidateRules() {
  const qc = useQueryClient()
  const { tenant } = useSignedIn()
  return () => {
    void qc.invalidateQueries({ queryKey: [RULES, tenant, 'list'] })
    void qc.invalidateQueries({ queryKey: [RULES, tenant, 'one'] })
  }
}

/** A rule from a library template (`{template_code}`); for a code the tenant has, a new version. */
export function useCreateRule() {
  const invalidate = useInvalidateRules()
  return useIdempotentMutation({
    mutationFn: (body: RuleCreateRequest, idempotencyKey: string) =>
      unwrapReplayable(api.POST('/monitoring/rules', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: invalidate,
    gcTime: 0,
  })
}

/** A new `DRAFT` version of a rule, starting from its latest version. */
export function useCreateVersion(ruleId: string) {
  const invalidate = useInvalidateRules()
  return useIdempotentMutation({
    mutationFn: (body: VersionRequest, idempotencyKey: string) =>
      unwrapReplayable(
        api.POST('/monitoring/rules/{rule_id}/versions', {
          params: { path: { rule_id: ruleId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: invalidate,
    gcTime: 0,
  })
}

/**
 * Tune a `DRAFT` or `SIMULATED` version. A PATCH carries no `Idempotency-Key` (the API's
 * idempotency covers POST only; an edit sets values, so a retry leaves the same version).
 */
export function useUpdateVersion(ruleId: string, v: number) {
  const invalidate = useInvalidateRules()
  return useMutation({
    mutationFn: (body: VersionRequest) =>
      unwrap(api.PATCH('/monitoring/rules/{rule_id}/versions/{v}', { params: { path: { rule_id: ruleId, v } }, body })),
    onSuccess: invalidate,
    gcTime: 0,
  })
}

/** Ask for a simulation of a version over a range of Mexico City days (202: it runs as a job). */
export function useSimulate(ruleId: string, v: number) {
  const invalidate = useInvalidateRules()
  return useIdempotentMutation({
    mutationFn: (body: SimulateRequest, idempotencyKey: string) =>
      unwrapReplayable(
        api.POST('/monitoring/rules/{rule_id}/versions/{v}/simulate', {
          params: { path: { rule_id: ruleId, v } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: invalidate,
    gcTime: 0,
  })
}

/** A lifecycle step; reject carries a reason and activate a date and an optional product. */
export type Transition =
  | { action: 'SUBMIT' | 'APPROVE' | 'RETIRE' }
  | { action: 'REJECT'; body: RejectRequest }
  | { action: 'ACTIVATE'; body: ActivateRequest }

function sendTransition(ruleId: string, v: number, transition: Transition, idempotencyKey: string) {
  const init = { params: { path: { rule_id: ruleId, v } }, headers: { 'Idempotency-Key': idempotencyKey } }
  switch (transition.action) {
    case 'SUBMIT':
      return unwrapReplayable(api.POST('/monitoring/rules/{rule_id}/versions/{v}/submit', init))
    case 'APPROVE':
      return unwrapReplayable(api.POST('/monitoring/rules/{rule_id}/versions/{v}/approve', init))
    case 'RETIRE':
      return unwrapReplayable(api.POST('/monitoring/rules/{rule_id}/versions/{v}/retire', init))
    case 'REJECT':
      return unwrapReplayable(
        api.POST('/monitoring/rules/{rule_id}/versions/{v}/reject', { ...init, body: transition.body }),
      )
    case 'ACTIVATE':
      return unwrapReplayable(
        api.POST('/monitoring/rules/{rule_id}/versions/{v}/activate', { ...init, body: transition.body }),
      )
  }
}

/**
 * Move a version through the lifecycle (spec section 9.4). The API decides who may: a refusal is
 * a 409 `RULE_TRANSITION_NOT_ALLOWED` whose `blocked_reasons` say why.
 */
export function useTransition(ruleId: string, v: number) {
  const invalidate = useInvalidateRules()
  return useIdempotentMutation({
    mutationFn: (transition: Transition, idempotencyKey: string) => sendTransition(ruleId, v, transition, idempotencyKey),
    onSuccess: invalidate,
    gcTime: 0,
  })
}
