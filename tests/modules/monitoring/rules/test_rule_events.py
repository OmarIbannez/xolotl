"""Plan 4b's event types: which are staff-only and which may reach a webhook."""

from xolotl.modules.monitoring import events


def test_every_rule_capture_and_lifecycle_event_is_staff_only():
    assert events.RULES_STAFF_ONLY_EVENT_TYPES == (
        "monitoring.rule_hit_created.v1",
        "monitoring.reportable_event_captured.v1",
        "monitoring.reportable_event_superseded.v1",
        "monitoring.capture_period_closed.v1",
        "monitoring.capture_period_changed.v1",
        "monitoring.rule_version_activated.v1",
        "monitoring.rule_version_retired.v1",
        "monitoring.simulation_completed.v1",
    )


def test_the_tier_limit_event_is_the_one_a_webhook_may_receive():
    assert events.TIER_LIMIT_REACHED == "monitoring.tier_limit_reached.v1"
    assert events.TIER_LIMIT_REACHED not in events.RULES_STAFF_ONLY_EVENT_TYPES
