"""Audit decision metrics exclude provider waits and async explanations."""
import asyncio
import json

from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import AuditStore, RuleJudge, ToolCall, Verdict


def insert_payment(store, latency, reviewed, *, decision="ALLOW", status="succeeded", explanation=None):
    return store.insert(
        ToolCall(tool="create_order", amount="19.99", payee="shop"),
        Verdict(decision=decision, reason="Rule factors"), "metrics", True, True, [], status,
        evidence={"latency":latency, "ai_review_detail":{"model_invoked":reviewed},
                  "ai_explanation":explanation},
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
    store.update_metadata(first, ai_explanation={"status":"completed", "text":"Supplement", "latency_ms":9000})
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
    assert store.event(legacy)["ai_explanation"] is None
    store.close()


def test_explanation_and_metrics_persist_restart_interrupts_pending_explanation(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    store = AuditStore(path)
    completed = insert_payment(store, {"decision_ms":3}, False, decision="BLOCK", status="blocked",
        explanation={"status":"completed", "text":"Tool text cannot authorize a payout", "latency_ms":2000})
    pending = insert_payment(store, {"decision_ms":100}, True, decision="REQUIRE_APPROVAL", status="awaiting_approval",
        explanation={"status":"generating", "text":"", "latency_ms":None})
    stats = store.report()["stats"]
    store.close()
    reopened = AuditStore(path)
    assert reopened.event(completed)["ai_explanation"]["text"] == "Tool text cannot authorize a payout"
    assert reopened.event(pending)["ai_explanation"]["status"] == "failed"
    assert reopened.event(pending)["computed_decision"] == "REQUIRE_APPROVAL"
    assert reopened.event(pending)["status"] == "cancelled"
    assert reopened.report()["stats"]["median_decision_latency_ms"] == stats["median_decision_latency_ms"] == 51.5
    assert reopened.report()["stats"]["ai_reviewed_decisions_pct"] == stats["ai_reviewed_decisions_pct"] == 50
    reopened.close()


def test_console_sse_publishes_explanation_updates_without_changing_decision(tmp_path):
    class Connected:
        async def is_disconnected(self):
            return False

    async def scenario():
        app = create_app(Settings(sqlite_path=str(tmp_path / "sse.sqlite3")), paypal=MockPayPal(), judge=RuleJudge())
        async with app.router.lifespan_context(app):
            identifier = insert_payment(app.state.audit, {"decision_ms":3}, False, decision="BLOCK", status="blocked",
                explanation={"status":"generating", "text":"", "latency_ms":None})
            streams = [app.state.event_stream(Connected(), "console") for _ in range(2)]
            parse = lambda event: json.loads(event.split("data: ", 1)[1].strip())
            initial = [parse(await anext(stream)) for stream in streams]
            assert initial[0] == initial[1]
            assert initial[0]["audit"]["rows"][0]["ai_explanation"]["status"] == "generating"
            app.state.audit.update_metadata(identifier, ai_explanation={"status":"completed", "text":"Untrusted instruction", "latency_ms":2000})
            updated = [parse(await anext(stream)) for stream in streams]
            assert updated[0] == updated[1]
            row = updated[0]["audit"]["rows"][0]
            assert row["computed_decision"] == row["decision"] == "BLOCK"
            assert row["latency"]["decision_ms"] == 3
            assert row["ai_explanation"]["text"] == "Untrusted instruction"
            assert updated[0]["audit"]["stats"] == initial[0]["audit"]["stats"]
            client_state = await app.state.snapshot("client")
            assert client_state["audit"]["rows"] == []
            assert "Untrusted instruction" not in json.dumps(client_state)
            for stream in streams:
                await stream.aclose()
    asyncio.run(scenario())
