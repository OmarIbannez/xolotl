# SP-01c — Events, Jobs, Workflows and the Worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the kernel its asynchronous backbone: a transactional outbox with an in-process dispatcher and exactly-once consumption, a Postgres job queue with leases, retries and cron schedules, a step-table workflow runner that survives restarts, signed webhooks with retries and a dead-letter view, in-app notifications, and the `xolotl worker` process that runs it all.

**Architecture:** Events are rows in `outbox` written inside the publisher's transaction and claimed by the dispatcher with `FOR UPDATE SKIP LOCKED` in `seq` order, one key at a time; each subscriber runs in its own transaction under the event's tenant context and records `event_consumption(consumer, event_id)` so redelivery is idempotent. Jobs are rows claimed the same way with a lease and a heartbeat; schedules enqueue jobs with a slot-keyed dedup key. Workflows are declared as ordered pure steps; the runner persists every step attempt in `workflow_step`, moves the run between `RUNNING`, `WAITING` and terminal states, and uses jobs for both advancement and timers. Webhooks are a subscriber that fans events out into `webhook_delivery` rows plus a delivery job that signs the payload with the endpoint's encrypted secret. Everything sits behind the `EventBus`, `JobQueue`, `WorkflowRunner` and `Notifier` ports from the program spec so Kafka or Temporal can replace the adapters later. One process, `xolotl worker`, ticks the dispatcher, the job runner (with the reaper) and the scheduler.

**Tech Stack:** 1a/1b stack plus `croniter` (runtime) and `respx` plus `types-croniter` (dev).

**Spec:** `docs/superpowers/specs/2026-10-18-sp01-platform-kernel-design.md` §9 (tables, interfaces, dispatcher/webhook/job/scheduler/workflow semantics, kernel events), §3 (`WORKER_TICK_SECONDS`, `WEBHOOK_TIMEOUT_SECONDS`), §10 (`worker`), §12 rows `test_outbox.py`, `test_webhooks.py`, `test_jobs.py`, `test_workflows.py`, §13 exit criteria; program spec D8, D9, D20.

**Deployment gates:** before production, (1) the `idempotency.purge` job runs for every tenant (the opportunistic purge in plan 1a only clears a tenant's expired keys when that tenant makes its next request), and (2) a monthly job keeps the `audit_event` partitions months ahead of the calendar and first drains any rows that fell into `audit_event_default` (an API that runs past the three months `db upgrade` creates writes there, and Postgres then refuses to create that month's partition: `ensure_partitions` logs the month at ERROR and skips it, so `db upgrade` and `serve` carry on, but the month gets no partition and its rows keep landing in `audit_event_default` until the job drains them).

## Global Constraints

- Everything in plans 1a and 1b's Global Constraints still binds.
- New configuration keys: `WORKER_TICK_SECONDS` (float, default `1.0`), `WEBHOOK_TIMEOUT_SECONDS` (int, default `5`), `WORKER_ID` (default `f"{hostname}:{pid}"`).
- Tables exactly as spec §9.1, every one a tenant table with forced RLS and an isolation factory; `outbox.seq` is `bigserial` with `UNIQUE (seq)`; `job_dedup` is a partial unique index on `(tenant_id, kind, key) WHERE key IS NOT NULL AND status IN ('PENDING','RUNNING')`; `webhook_endpoint.secret` is `EncryptedStr` (plan 1b).
- Dispatcher: claims `PENDING` rows with `created_at < now() - interval '1 second'` and `(locked_until IS NULL OR locked_until < now())`, ordered by `seq`, `FOR UPDATE SKIP LOCKED`, `LIMIT batch`; a row whose `(tenant_id, key)` has an earlier `PENDING` row is skipped this cycle; each handler runs in its own `tenant_session(TenantContext.system(tenant_id, "dispatcher"))`; success inserts `event_consumption`; an existing consumption row skips the handler; a handler exception increments `attempts`, sets `last_error` and `locked_until = now() + min(2^attempts, 60) seconds`; after `attempts >= 10` the row is `DEAD`; a row is `PUBLISHED` once every subscribed consumer has a consumption row.
- Job runner: `claim` locks `PENDING AND run_after <= now()` rows with `FOR UPDATE SKIP LOCKED`, sets `RUNNING`, `locked_by = worker_id`, `locked_until = now() + lease` (default 60 s); a heartbeat extends `locked_until` every `lease / 2` while the handler runs; `fail` schedules `retry_at = now() + min(2^attempts minutes, 60 min)` or marks `DEAD` at `max_attempts`; the reaper returns `RUNNING` rows with `locked_until < now()` to `PENDING`.
- Scheduler: each tick, enabled schedules with `next_run_at <= now()` enqueue their job with `key = f"{name}:{next_run_at.isoformat()}"` and advance `next_run_at` with `croniter` in `tz`; a tick that crashes between the enqueue and the advance cannot double-enqueue (dedup key).
- Workflow runner: `start` inserts `RUNNING` and enqueues `workflow.advance`; `advance` runs the current step in one transaction keyed `(run_id, step_id, attempt)`, persists the `workflow_step` row and the new `state`, then enqueues the next step, sets `WAITING` with `wake_at` (plus a timer job keyed `f"{run_id}:{step_id}:{wake_at}"`), or finishes; `signal` stores the row and enqueues `advance` only if the run is waiting for that name; a duplicate or early signal is stored unconsumed and ignored; a timer firing while still waiting calls the step with `timed_out=True`; `UNIQUE (tenant_id, definition, business_key)` makes `start` idempotent (`DomainError` on a duplicate).
- Webhooks: fan-out consumer `webhooks` creates one `webhook_delivery` per `ACTIVE` endpoint whose `event_types` contains the type or `*` and enqueues `webhook.deliver`; delivery POSTs the event JSON with `Content-Type: application/json`, `X-Xolotl-Event-Id`, `X-Xolotl-Event-Type`, `X-Xolotl-Timestamp: <unix seconds>`, `X-Xolotl-Signature: v1=<hex hmac_sha256(secret, f"{timestamp}.{body}")>`; 2xx → `DELIVERED`; otherwise retry with backoff `min(2^attempt minutes, 60 min)` until 24 hours after `created_at`, then `DEAD`; endpoint URLs must be `https://` (or `http://` only when `ENV != prod`) and must not resolve to loopback, link-local or private ranges in `prod`.
- Kernel events published by the tenants service inside the same transaction as the write: `tenant.created.v1`, `staff.created.v1`, `api_key.created.v1`, `api_key.revoked.v1`; `POST /v1/ping` publishes `test.ping.v1`. Payloads carry ids only (D8), never PII.
- Idempotency purge: job kind `idempotency.purge` deletes expired `idempotency_key` rows, scheduled hourly for every tenant by a platform schedule created at tenant creation.
- Audit actions added: `webhook_endpoint.created`, `webhook_endpoint.deleted`, `webhook_delivery.retried`.
- `xolotl worker` handles SIGTERM/SIGINT by finishing the current tick and releasing claimed jobs (`fail` with `retry_at = now()` and no attempt increment) before exiting.

## Deviations from the sub-spec (decided here)

- `EventBus.dispatch_pending`, `JobQueue.claim/complete/fail` and `WorkflowRunner.advance` take no caller session (they open their own, like the key provider); `publish`, `enqueue`, `schedule`, `start`, `signal` run in the caller's transaction as the spec says.
- The spec's `Handler = Callable[[Session, Event], None]` is kept; subscribers are registered at composition time (`app.py`/`worker.py`) through `register_subscriber(event_type, handler, consumer=...)` on a module-level registry so the API process and the worker share one table of subscribers.
- Webhook endpoint secrets are generated by the platform (`whsec_<32 hex>`), returned once on creation, stored as `EncryptedStr`; the spec left generation unspecified.
- `notification` is written by an `InAppNotifier` adapter of the `Notifier` port (`notify(session, *, template, data, recipient_user_id=None, recipient_role=None)`); reading notifications is a console concern for a later plan (one `GET /v1/notifications` list for the current user is included here because the console's shell needs it).

## Review Focus

1. Two worker processes dispatching the same outbox concurrently must never run a consumer twice for one event, and must preserve per-key order even when a key's earlier event is retrying. Test in E2 (two threads with separate connections, forced handler failure).
2. A job whose handler hangs past its lease must be reclaimable by another worker without the first worker's late `complete()` overwriting the second run's state. Test in E3 (lease fencing by `locked_by` and a `lock_token`).
3. A workflow whose process dies between persisting a step and enqueuing the next must resume from the step table on the next `advance`, running the step exactly once. Test in E4 (kill the runner between the two writes with a monkeypatched hook).
4. Webhook signatures must be reproducible by a receiver from the raw body (no re-serialisation), and a replayed delivery must carry a new timestamp and signature. Test in E6.
5. A schedule in `America/Mexico_City` across the DST-free 2026 boundary and a `0 0 * * *` schedule must enqueue exactly once per slot even when the worker was down for several slots (catch-up policy: run the missed slots? No — skip to the next future slot, recording `last_run_at`; document it). Test in E3.

## File structure

```
src/xolotl/platform/
  config.py                                (+ WORKER_TICK_SECONDS, WEBHOOK_TIMEOUT_SECONDS, WORKER_ID)
  enums.py                                 (+ OutboxStatus, DeliveryStatus, EndpointStatus, JobStatus, RunStatus, StepStatus)
  events/__init__.py                       re-exports
  events/models.py                         OutboxRow, EventConsumption, WebhookEndpoint, WebhookDelivery, Notification
  events/event.py                          Event (pydantic), event_from_row, row_from_event
  events/registry.py                       register_subscriber, subscribers_for, consumers()
  events/bus.py                            OutboxEventBus (publish, dispatch_pending, replay) + claim SQL constants
  events/notifier.py                       Notifier protocol, InAppNotifier
  events/webhooks.py                       fanout handler, deliver job handler, sign(), verify_signature(), backoff()
  jobs/__init__.py                         re-exports
  jobs/models.py                           JobRow, JobSchedule
  jobs/queue.py                            Job (dataclass), PostgresJobQueue (enqueue, claim, heartbeat, complete, fail, reap, schedule), register_job_handler
  jobs/runner.py                           JobRunner.run_once(worker_id) with heartbeat thread
  jobs/scheduler.py                        Scheduler.tick()
  workflows/__init__.py                    re-exports
  workflows/models.py                      WorkflowRunRow, WorkflowStepRow, WorkflowSignalRow
  workflows/definition.py                  StepInput, StepResult, WorkflowDefinition, register_workflow, workflow()
  workflows/runner.py                      PostgresWorkflowRunner (start, advance, signal, get_run) + the advance/timer job handlers
src/xolotl/worker.py                       Worker(loops, tick, signals) and build_worker()
src/xolotl/modules/tenants/service.py      (publishes kernel events; creates the purge schedule)
src/xolotl/modules/tenants/routes_tenants.py (ping publishes test.ping.v1)
src/xolotl/modules/tenants/routes_webhooks.py, routes_notifications.py, schemas.py (+)
src/xolotl/migrations/versions/0004_events_jobs_workflows.py
src/xolotl/app.py, src/xolotl/cli.py        (composition; `worker` command)
tests/platform/test_outbox.py, test_jobs.py, test_scheduler.py, test_workflows.py, test_webhooks.py, test_notifier.py, test_worker.py
tests/modules/tenants/test_webhook_routes.py, test_kernel_events.py; tests/isolation/factories.py (+ eight factories)
```

---

### Task E1: Tables, models, enums and the `Event` model

**Files:** `platform/enums.py` (+6 enums), `events/models.py`, `jobs/models.py`, `workflows/models.py`, `events/event.py`, migration `0004_events_jobs_workflows.py`, `src/xolotl/models.py` (imports), `tests/isolation/factories.py` (+ factories for outbox, event_consumption, webhook_endpoint, webhook_delivery, job, job_schedule, workflow_run, workflow_step, workflow_signal, notification), `tests/platform/test_event_model.py`.

**Interfaces produced:** the ORM classes named above with attributes equal to the spec's columns; `Event` exactly as spec §9.2 with `model_config = ConfigDict(frozen=True)`; `event_from_row(row: OutboxRow) -> Event` and `row_from_event(event: Event) -> OutboxRow` (`payload` jsonb holds `{"source", "subject", "time", "correlation_id", "policy_snapshot_id", "data"}`; `type`, `key`, `id`, `tenant_id` are columns); every enum UPPERCASE.

- [ ] Tests: the schema-sync test (compare_metadata) is the arbiter; `test_event_model.py` round-trips an `Event` through the row mapping and rejects a `data` containing floats (reuse Task 7's rule: ids only, no floats) and a `key` longer than 200 chars; the behavioural isolation harness runs for all ten tables (factories create parents as needed: a delivery needs an endpoint, a step and a signal need a run).
- [ ] Migration: all ten tables, RLS, the `job_dedup` partial unique index, indexes `ix_outbox_pending (status, created_at) WHERE status = 'PENDING'`, `ix_job_claim (status, run_after)`, `ix_workflow_run_wake (status, wake_at)`, `ix_webhook_delivery_next (status, next_attempt_at)`; `webhook_endpoint.secret` as `bytea` (`EncryptedStr`) — this task therefore depends on plan 1b Task B3 being merged; if it is not yet merged when E1 starts, declare `secret` as `LargeBinary` with a `# TODO(1b): EncryptedStr` and let E6 flip it (ruling to be recorded).
- [ ] Commit `feat(platform): tables and models for outbox, jobs, schedules, workflows, webhooks and notifications`.

---

### Task E2: The outbox event bus and dispatcher

**Files:** `events/registry.py`, `events/bus.py`, `tests/platform/test_outbox.py`.

**Interfaces produced:**

```python
Handler = Callable[[Session, Event], None]
def register_subscriber(event_type: str, handler: Handler, *, consumer: str) -> None   # "*" subscribes to every type
def subscribers_for(event_type: str) -> list[tuple[str, Handler]]                    # (consumer, handler), registration order
def reset_subscribers() -> None                                                        # tests

class OutboxEventBus:
    def __init__(self, session_factory: SessionFactory, *, max_attempts: int = 10, clock_fn=clock.now) -> None
    def publish(self, session: Session, event: Event) -> None            # tenant from bound_context must equal event.tenant_id
    def dispatch_pending(self, *, batch: int = 200) -> int               # returns rows handled (published, retried or dead)
    def replay(self, *, since: datetime, types: Sequence[str], consumer: str, tenant_id: UUID | None = None) -> int
```

Claim SQL (platform-operator session, since the dispatcher spans tenants; the dispatcher connects with the app role and a `PLATFORM_OPERATOR` context sees no tenant rows under RLS — therefore the dispatcher claims **per tenant**: first `SELECT DISTINCT tenant_id FROM outbox WHERE status = 'PENDING'` is impossible under RLS too). Ruling: the dispatcher iterates tenants through a platform-context read of `tenant` (the `tenant` policy admits `PLATFORM_OPERATOR`), then for each tenant opens `tenant_session(TenantContext.system(tenant_id, "dispatcher"))` and claims:

```sql
SELECT seq, id FROM outbox
WHERE status = 'PENDING' AND created_at < now() - interval '1 second'
  AND (locked_until IS NULL OR locked_until < now())
ORDER BY seq
LIMIT :batch
FOR UPDATE SKIP LOCKED
```
then filters out rows whose `key` has an earlier `PENDING` row (`SELECT key, min(seq) FROM outbox WHERE status='PENDING' GROUP BY key`), sets `locked_until = now() + lease` and commits the claim; each handler then runs in its own transaction; consumption rows are inserted with `ON CONFLICT DO NOTHING`; the row becomes `PUBLISHED` when every registered consumer for its type has a consumption row, or `DEAD` after `max_attempts`. `replay` selects rows `PUBLISHED` (or `DEAD`) with `created_at >= since AND type IN types` for one tenant or all, deletes the consumer's consumption rows for them and re-runs the handler for that consumer only (not the others), returning the count.

- [ ] Tests (real DB): publish inside a transaction is invisible until commit and rolls back with it; dispatch runs each subscriber once and marks `PUBLISHED`; a handler raising twice then succeeding ends `PUBLISHED` with `attempts == 2` and the other consumer ran once; `max_attempts` → `DEAD` with `last_error`; per-key ordering (three events on one key, the first failing: the others are not dispatched until it succeeds; a different key proceeds); exactly-once under forced redelivery (clear `locked_until` after a crash simulated by raising after the consumption insert but before the status update; redispatch skips the handler); two dispatchers in threads over one outbox with 50 events: each handler call count equals 50 and order per key holds; `replay` re-runs only the named consumer; a `*` subscriber receives every type; publishing with a mismatched tenant context raises.
- [ ] Commit `feat(events): transactional outbox, dispatcher with per-key ordering and exactly-once consumption, replay`.

---

### Task E3: Job queue, runner, reaper and cron scheduler

**Files:** `jobs/queue.py`, `jobs/runner.py`, `jobs/scheduler.py`, `platform/config.py` (+ `WORKER_TICK_SECONDS`, `WORKER_ID`), `pyproject.toml` (+ `croniter`, dev `types-croniter`), `tests/platform/test_jobs.py`, `tests/platform/test_scheduler.py`.

**Interfaces produced:**

```python
@dataclass(frozen=True, slots=True)
class Job: id: UUID; tenant_id: UUID; kind: str; key: str | None; payload: dict[str, JsonValue]; attempts: int; max_attempts: int; lock_token: UUID

JobHandler = Callable[[Session, Job], None]
def register_job_handler(kind: str, handler: JobHandler) -> None
def job_handler(kind: str) -> JobHandler            # raises KeyError with the kind name

class PostgresJobQueue:
    def __init__(self, session_factory: SessionFactory, *, clock_fn=clock.now) -> None
    def enqueue(self, session: Session, *, kind: str, payload: dict[str, JsonValue], run_after: datetime | None = None, key: str | None = None, max_attempts: int = 8) -> UUID | None
        # returns None when the dedup index rejects a duplicate (ON CONFLICT DO NOTHING on the partial index)
    def claim(self, *, worker_id: str, tenant_id: UUID, kinds: Sequence[str] | None, limit: int, lease: timedelta) -> list[Job]
    def heartbeat(self, job: Job, *, lease: timedelta) -> bool          # extends locked_until if lock_token still matches; False means the lease was lost
    def complete(self, job: Job) -> bool                                 # DONE only if lock_token matches; False means another worker owns it now
    def fail(self, job: Job, error: str, *, retry_at: datetime | None) -> bool
    def reap(self, *, tenant_id: UUID) -> int                            # RUNNING with locked_until < now() -> PENDING, attempts unchanged
    def schedule(self, session: Session, *, name: str, cron: str, tz: str, kind: str, payload: dict[str, JsonValue]) -> UUID   # upsert by (tenant, name); computes next_run_at
    def tenants_with_work(self) -> list[UUID]                            # platform-context read of tenant ids (see E2 ruling)

class JobRunner:
    def __init__(self, queue: PostgresJobQueue, session_factory: SessionFactory, *, worker_id: str, lease: timedelta = timedelta(seconds=60), kinds: Sequence[str] | None = None) -> None
    def run_once(self, *, limit: int = 10) -> int                        # for each tenant: reap, claim, run handlers with a heartbeat thread, complete/fail; returns jobs run

class Scheduler:
    def __init__(self, queue: PostgresJobQueue, session_factory: SessionFactory, *, clock_fn=clock.now) -> None
    def tick(self) -> int                                                 # enqueues due schedules per tenant; returns jobs enqueued
```

Lease fencing (Review Focus 2): `job.lock_token` is a fresh UUID written on `claim` into a new column `lock_token uuid` (add to the migration in E1 — E1 must include it; if E1 is already merged, E3 adds migration `0005_job_lock_token`); `heartbeat`, `complete` and `fail` update `WHERE id = :id AND tenant_id = :tenant AND lock_token = :token`. Catch-up policy (Review Focus 5): a schedule whose `next_run_at` is in the past enqueues once for that slot and advances to the next slot **after now**, skipping missed slots; `last_run_at` records the slot enqueued. The dispatcher/runner/scheduler iterate tenants via the platform-context `tenant` read (E2 ruling).

- [ ] Tests (real DB): enqueue+claim round trip; `SKIP LOCKED` — two claimers in threads never get the same job; lease expiry: a job claimed with a 1-second lease, not heart-beaten, is returned by `reap` and re-claimed by another worker whose `complete` wins while the first worker's late `complete` returns False; retry then success (handler raises twice, succeeds on the third: `attempts == 3`, `DONE`); `DEAD` at `max_attempts` with `last_error`; dedup key: enqueue twice with the same key while `PENDING` returns `None` the second time, and again after `DONE` enqueues anew; `run_after` in the future is not claimed; handler runs under the job's tenant context (assert `current_setting('app.tenant_id')`); heartbeat extends `locked_until`. Scheduler: a `*/5 * * * *` schedule enqueues exactly one job per tick even when `tick()` runs twice in the same slot (dedup); after a simulated downtime of three slots it enqueues once and `next_run_at` is in the future; `0 0 * * *` in `America/Mexico_City` computes `next_run_at` at local midnight (assert the UTC offset); an invalid cron expression is rejected by `schedule` with `ValidationFailedError`; a disabled schedule is skipped.
- [ ] Commit `feat(jobs): Postgres job queue with fenced leases, retries, reaper, handler registry and cron scheduler`.

---

### Task E4: The step-table workflow runner

**Files:** `workflows/definition.py`, `workflows/runner.py`, `tests/platform/test_workflows.py`.

**Interfaces produced:**

```python
class StepInput(BaseModel): signal: str | None = None; payload: dict[str, JsonValue] = {}; timed_out: bool = False
class StepResult(BaseModel): state: dict[str, JsonValue]; done: bool = False; wait_for_signal: str | None = None; wake_at: datetime | None = None
Step = Callable[[Session, WorkflowRunRow, dict[str, JsonValue], StepInput], StepResult]
@dataclass(frozen=True) class WorkflowDefinition: name: str; steps: tuple[tuple[str, Step], ...]; timeout: timedelta | None = None
def register_workflow(definition: WorkflowDefinition) -> None;  def workflow(name: str) -> WorkflowDefinition

class PostgresWorkflowRunner:
    def __init__(self, session_factory: SessionFactory, queue: PostgresJobQueue, *, clock_fn=clock.now) -> None
    def start(self, session: Session, definition: str, *, business_key: str, input: dict[str, JsonValue]) -> UUID   # DomainError on duplicate business_key
    def signal(self, session: Session, run_id: UUID, name: str, payload: dict[str, JsonValue]) -> None
    def advance(self, run_id: UUID, tenant_id: UUID) -> None            # job handler "workflow.advance"; also "workflow.timer"
    def get_run(self, session: Session, run_id: UUID) -> WorkflowRunRow
```

Semantics per the Global Constraints. `advance` locks the run row `FOR UPDATE`, determines the current step and `attempt = 1 + count(workflow_step rows for that step)`, consumes a matching unconsumed signal if waiting, runs the step, writes the step row (`DONE`/`FAILED` with `error`), updates `state`, `current_step`, `status`, `wake_at`, and enqueues the next job in the same transaction (the outbox pattern applied to jobs). A step exception marks the step `FAILED`, re-raises so the job retries (bounded by the job's `max_attempts`), and after the job dies marks the run `FAILED`. `timeout` on the definition sets `wake_at` for a `WAITING` run when no step-level `wake_at` exists.

- [ ] Tests (real DB, a test definition with steps `collect` → wait for signal `approve` with a 1-hour timeout → `finish`): a two-step run completes with `DONE` and two step rows; the run "survives a restart" — monkeypatch a hook to raise after the step row commit but before the enqueue (simulate a crash), then call `advance` again: the step is not re-run (attempt count stays 1) and the run proceeds; duplicate signal: the second `approve` is stored unconsumed and `advance` ignores it; early signal (before the run waits) is stored and consumed when the run reaches the wait; timeout path: the timer job fires with `timed_out=True` and the step decides to fail/finish; `business_key` uniqueness raises `DomainError`; `get_run` under another tenant raises `NotFoundError` (RLS).
- [ ] Commit `feat(workflows): step-table workflow runner with signals, timers and restart safety`.

---

### Task E5: Worker process, composition, kernel events, notifier and the idempotency purge

**Files:** `src/xolotl/worker.py`, `src/xolotl/cli.py` (`worker` command), `src/xolotl/app.py` (bus/queue/runner on `AppState`; subscribers and job handlers registered in one `wire_platform()` used by both processes), `events/notifier.py`, `modules/tenants/service.py` (publish kernel events; create the hourly `idempotency.purge` schedule on tenant creation), `modules/tenants/routes_tenants.py` (ping publishes `test.ping.v1`), `modules/tenants/routes_notifications.py` (`GET /v1/notifications` for the current staff user, cursor-paginated, `POST /v1/notifications/{id}/read`), `platform/api/idempotency.py` (`purge_expired(session) -> int`), `tests/platform/test_worker.py`, `tests/platform/test_notifier.py`, `tests/modules/tenants/test_kernel_events.py`.

- [ ] `Worker` runs `dispatch_pending`, `JobRunner.run_once`, `Scheduler.tick` each tick in that order, sleeps `WORKER_TICK_SECONDS` between ticks (interruptible), installs SIGTERM/SIGINT handlers that set a stop flag; `run(max_ticks: int | None = None)` for tests; on stop, jobs still running are allowed to finish (the heartbeat thread keeps the lease).
- [ ] `wire_platform(session_factory, settings) -> PlatformServices` creates the bus, queue, runner, workflow runner, notifier and object store, registers the webhook fan-out subscriber (E6), the `workflow.advance`/`workflow.timer` job handlers and the `idempotency.purge` handler; `build_app` and `build_worker` both call it.
- [ ] Kernel events: `create_tenant` publishes `tenant.created.v1` (`data={"tenant_id", "slug", "env"}`) and `staff.created.v1`; `create_staff` publishes `staff.created.v1`; `create_api_key`/`revoke_api_key` publish their events; keys are the entity ids. `POST /v1/ping` publishes `test.ping.v1` with `key = actor_id`.
- [ ] Tests: the worker processes a published `test.ping.v1` end to end (a test subscriber records it) within three ticks; SIGTERM stops the loop after the current tick; `idempotency.purge` removes expired rows and leaves live ones; notifications are listed newest first for the recipient only and `read` sets `read_at`; kernel events exist after each service call with ids-only payloads.
- [ ] Commit `feat(worker): worker process, platform wiring, kernel events, notifications and idempotency purge`.

---

### Task E6: Webhooks: endpoints, fan-out, signed delivery, retries, dead letters, routes

**Files:** `events/webhooks.py`, `modules/tenants/routes_webhooks.py`, `schemas.py` (+), `tests/platform/test_webhooks.py`, `tests/modules/tenants/test_webhook_routes.py`. Depends on plan 1b Task B3 (`EncryptedStr`) and E5.

**Interfaces produced:** `generate_webhook_secret() -> str` (`whsec_<32 hex>`), `sign(secret: str, timestamp: int, body: bytes) -> str` (`v1=<hex>`), `verify_signature(secret, header, timestamp, body, *, tolerance=timedelta(minutes=5), now=...) -> bool` (constant-time; documented for receivers), `backoff(attempt: int) -> timedelta`, `fanout(session, event)` (consumer `webhooks`), `deliver(session, job)` (job kind `webhook.deliver`, payload `{"delivery_id"}`), `WEBHOOK_TIMEOUT_SECONDS` from settings; routes `POST /v1/webhooks/endpoints` (ADMIN; `{url, event_types}` → 201 with the secret once), `GET /v1/webhooks/endpoints`, `DELETE /v1/webhooks/endpoints/{id}` (204, sets `DISABLED`; deliveries kept), `GET /v1/webhooks/deliveries?status=&endpoint_id=` (cursor), `POST /v1/webhooks/deliveries/{id}/retry` (resets a `DEAD`/`PENDING` delivery to `PENDING`, `next_attempt_at = now()`, enqueues `webhook.deliver`, audits).

- [ ] Tests (`respx` to mock the receiver; real DB): fan-out creates one delivery per matching active endpoint (`*` matches all; disabled endpoints skipped) and enqueues one job per delivery; delivery sends the exact headers and a signature a receiver can verify from the raw body (verify with `verify_signature` on the mocked request's content); 2xx → `DELIVERED` with `response_code`; 500 → retry scheduled with `backoff(1) == 2 min`, attempts increment; after 24 hours of failures → `DEAD`; manual retry re-enqueues with a new timestamp and signature; URL validation (https required in prod; private ranges refused in prod via a resolver seam, allowed in dev for localhost testing); the secret never appears in list responses or logs (redaction scan).
- [ ] Commit `feat(webhooks): endpoints, fan-out, HMAC-signed delivery with retries and dead letters, routes`.

---

### Task E7: Full gate, docs and errata

- [ ] `make ci` green; `docker build`; compose gains a `worker` service (`command: ["xolotl", "worker"]`, same env, `depends_on: db`); README documents the worker, webhooks (headers and signature verification snippet), schedules and notifications; spec errata for the deviations above (dispatcher iterates tenants under RLS; `lock_token`; catch-up policy; secret generation; notifications list route).
- [ ] Commit `docs: events/jobs/workflows guide, compose worker service and SP-01c errata`.

---

## Exit criteria for plan 1c (spec §13 rows owned here)

A sample event is dispatched exactly once across a forced redelivery and reaches a webhook with a valid signature; a failing webhook retries and lands in the dead-letter view; a job fails twice and succeeds on the third attempt; a cron schedule fires once per slot; a two-step workflow survives a worker restart; `xolotl worker` runs all three loops and stops cleanly on SIGTERM; `make ci` green.
