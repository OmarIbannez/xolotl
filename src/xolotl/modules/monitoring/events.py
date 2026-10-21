"""Monitoring's domain events (SP-04 spec §14): types only, ids-only payloads.

Plan 4b's types: every one but ``TIER_LIMIT_REACHED`` is staff-only (a hit, a
capture, a period or a rule change must never reach a webhook, PRV-502), and
``RULES_STAFF_ONLY_EVENT_TYPES`` is the tuple ``register_rules`` marks with
``register_staff_only``. ``TIER_LIMIT_REACHED`` is webhook-safe: ids and codes,
never an amount.
"""

RULE_HIT_CREATED = "monitoring.rule_hit_created.v1"
TIER_LIMIT_REACHED = "monitoring.tier_limit_reached.v1"
REPORTABLE_EVENT_CAPTURED = "monitoring.reportable_event_captured.v1"
REPORTABLE_EVENT_SUPERSEDED = "monitoring.reportable_event_superseded.v1"
CAPTURE_PERIOD_CLOSED = "monitoring.capture_period_closed.v1"
CAPTURE_PERIOD_CHANGED = "monitoring.capture_period_changed.v1"
RULE_VERSION_ACTIVATED = "monitoring.rule_version_activated.v1"
RULE_VERSION_RETIRED = "monitoring.rule_version_retired.v1"
SIMULATION_COMPLETED = "monitoring.simulation_completed.v1"

# A separate tuple beside plan 4a's STAFF_ONLY_EVENT_TYPES (reconciliation finding
# 14): B7's register_rules registers it, register_monitoring registers 4a's.
RULES_STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (
    RULE_HIT_CREATED,
    REPORTABLE_EVENT_CAPTURED,
    REPORTABLE_EVENT_SUPERSEDED,
    CAPTURE_PERIOD_CLOSED,
    CAPTURE_PERIOD_CHANGED,
    RULE_VERSION_ACTIVATED,
    RULE_VERSION_RETIRED,
    SIMULATION_COMPLETED,
)
