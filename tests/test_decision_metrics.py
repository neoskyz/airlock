"""Audit decision metrics measure only synchronous decisions."""
import asyncio
import json

from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import AuditStore, RuleJudge, ToolCall, Verdict


def insert_payment(store, latency, reviewed, *, decision="ALLOW", status="succeeded"):
    return store.insert(
        ToolCall(tool="create_order", amount="19.99", payee="shop"),
        Verdict(decision=decision, reason="Rule factors"), "metrics", True, True, [], status,
        evidence={"latency":latency, "ai_review_detail":{"model_invoked":reviewed}},
    )


def test_metrics_median_and_review_percentage_use_payment_decisions_only():
    store = AuditStore(":memory:")
    assert store.report()["stats"]["median_decision_latency_ms"] is None
    assert store.report()["stats"]["ai_reviewed_decisions_pct"] is None
    first = insert_payment(store, {"decision_ms":2, "end_to_end_ms":10000}, False)
    insert_payment(store, {"decision_ms":4}, True, decision="REQUIRE_APPROVAL", status="denied")
    insert_payment(store, {"decision_ms":8}, False, decision="BLOCK", status="blocked")
    insert_payment(store, {"decision_ms":1000}, False)  # Cached/rules reviews are not actual model calls.
    store.record_config_change("policy", {}, {})
    store.record_run_cancellation("cancelled", True, True)
    stats = store.report()["stats"]
    assert stats["median_decision_latency_ms"] == 6
    assert stats["ai_reviewed_decisions_pct"] == 25
    assert stats["decision_latency_samples"] == stats["ai_reviewed_decision_samples"] == 4
    store.record_approval(first, "approved")
    assert store.report()["stats"] == stats
    store.close()


def test_metrics_exclude_unknown_history_and_invalid_latencies():
    store = AuditStore(":memory:")
    legacy = insert_payment(store, {"total_ms":1234}, None)
    for value in (-1, float("nan"), float("inf"), "3", True):
        insert_payment(store, {"decision_ms":value}, None)
    assert store.report()["stats"]["median_decision_latency_ms"] is None
    assert store.report()["stats"]["ai_reviewed_decisions_pct"] is None
    insert_payment(store, {"decision_ms":3}, False)
    stats = store.report()["stats"]
    assert stats["median_decision_latency_ms"] == 3
    assert stats["ai_reviewed_decisions_pct"] == 0
    assert stats["decision_latency_samples"] == stats["ai_reviewed_decision_samples"] == 1
    store.close()


def test_metrics_persist_across_restart_and_approval_cancellation(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    store = AuditStore(path)
    insert_payment(store, {"decision_ms":3}, False, decision="BLOCK", status="blocked")
    pending = insert_payment(store, {"decision_ms":100}, True, decision="REQUIRE_APPROVAL", status="awaiting_approval")
    stats = store.report()["stats"]
    store.close()
    reopened = AuditStore(path)
    assert reopened.event(pending)["computed_decision"] == "REQUIRE_APPROVAL"
    assert reopened.event(pending)["status"] == "cancelled"
    assert reopened.report()["stats"]["median_decision_latency_ms"] == stats["median_decision_latency_ms"] == 51.5
    assert reopened.report()["stats"]["ai_reviewed_decisions_pct"] == stats["ai_reviewed_decisions_pct"] == 50
    reopened.close()
