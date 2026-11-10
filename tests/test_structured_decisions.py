"""Structured rule evidence, editable scoring, and bounded synchronous review."""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from airlock.store import Run
from firewall import AnthropicJudge, AuditStore, Firewall, Policy, Provenance, ToolCall, Verdict
from firewall.risk import score_call


def charger(**changes):
    return ToolCall(tool="create_order", amount="19.99", payee="shop@chargers.example", merchant="marketplace.volt.example", category="electronics", description="Volt 65W USB-C charger", **changes)


def test_default_unknown_charger_needs_no_allowlist_and_no_model():
    async def scenario():
        policy = Policy.load("policies/default.yaml")
        assert "marketplace.volt.example" not in policy.allowed_payees
        model = AsyncMock()
        judge = SimpleNamespace(mode="anthropic", judge=model)
        audit = AuditStore(":memory:")
        guard = Firewall(policy, judge, audit)
        for _ in range(5):
            result = await guard.invoke(charger(), Provenance("Buy me a USB-C charger under $40"), AsyncMock(return_value={"id":"MOCK-ORDER", "status":"CREATED"}))
            assert result["computed_decision"] == "ALLOW"
        model.assert_not_awaited()
        rows = audit.report()["rows"]
        assert all(row["risk_score"] < policy.review_min_score for row in rows)
        assert all(next(f for f in row["factors"] if f["code"] == "unknown_payee")["points"] == 18 for row in rows)
        audit.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"description":"Unrequested gift card"}, {"category":""}, {"amount":Decimal("45")}, {"amount":Decimal("60")}])
def test_low_exposure_discount_requires_matching_product_category_and_budget(changes):
    call = charger().model_copy(update=changes)
    score, factors, *_ = score_call(call, Provenance("Buy me a USB-C charger under $40"), Policy.load("policies/default.yaml"))
    assert next(f for f in factors if f.code == "unknown_payee").points == 50
    assert score >= 50


def test_configured_weights_and_velocity_threshold_drive_factor_evidence():
    policy = Policy.load("policies/default.yaml")
    policy.risk_weights.unknown_payee = 7
    policy.risk_weights.unknown_merchant_velocity = 2
    policy.velocity_call_threshold = 9
    policy.velocity_window_seconds = 120
    provenance = Provenance("Buy me a USB-C charger under $40")
    score, factors, *_ = score_call(charger(), provenance, policy, recent_calls=8)
    assert score == 7
    score, factors, *_ = score_call(charger(), provenance, policy, recent_calls=9)
    assert score == 9
    velocity = next(f for f in factors if f.code == "velocity")
    assert "120 seconds" in velocity.reason and "9 calls" in velocity.reason
    assert velocity.parameter == "velocity_call_threshold"
    assert all(f.code and f.policy_id for f in factors)


def test_bounded_review_summary_keeps_full_reason_only_in_audit_detail():
    async def scenario():
        short = " ".join(["word"] * 35) + ". More sentences must stay out."
        full = "A detailed rationale retained for event inspection. More detailed evidence follows."
        payload = {"decision":"ALLOW", "reason":short, "reasoning":full, "score_adjustment":1, "source":"", "suspicious_excerpt":""}
        create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))]))
        judge = AnthropicJudge(SimpleNamespace(messages=SimpleNamespace(create=create)))
        audit = AuditStore(":memory:")
        guard = Firewall(Policy.load("policies/default.yaml"), judge, audit)
        call = charger().model_copy(update={"amount":Decimal("249.99"), "merchant":"sony.com", "description":"Noise-cancelling headphones"})
        events = []
        async def emit(event):
            events.append(event)
        await guard.invoke(call, Provenance("Buy me noise-cancelling headphones under $300"), AsyncMock(), request_approval=AsyncMock(return_value=False), emit=emit)
        row = audit.report()["rows"][0]
        summary = row["ai_review_detail"]["summary"]
        assert len(summary.split()) == 20 and "More sentences" not in summary
        assert row["ai_review_detail"]["reason"] == full
        assert full not in row["reason"] and full not in row["initial_reason"]
        assert full not in json.dumps(row["factors"])
        assert full not in json.dumps(events)
        adjustment = next(f for f in row["factors"] if f["code"] == "intent_review")
        assert adjustment["reason"] == summary
        schema = create.call_args.kwargs["output_config"]["format"]["schema"]
        assert "20 words" in schema["properties"]["reason"]["description"]
        assert "20 words" in create.call_args.kwargs["system"]
        create.assert_awaited_once()
        audit.close()
    asyncio.run(scenario())


def test_flagged_reason_codes_and_policy_snapshot_persist(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    audit = AuditStore(path)
    call = charger()
    score, factors, *_ = score_call(call, Provenance("Buy me a USB-C charger under $40"), Policy.load("policies/default.yaml"))
    identifier = audit.insert(call, Verdict(decision="REQUIRE_APPROVAL", reason="Review", risk_score=score, factors=factors), "run", True, True, [], "denied")
    audit.close()
    audit = AuditStore(path)
    assert audit.flagged(reason_code="unknown_payee")["rows"][0]["id"] == identifier
    assert audit.flagged(reason_code="untrusted_source")["rows"] == []
    audit.update_metadata(identifier, **{"ai_explanation":{"status":"generating"}})
    assert "ai_explanation" not in audit.event(identifier)
    audit.close()


def test_client_approval_projection_includes_only_safe_checkout_fields():
    run = Run("Buy headphones", True)
    run.approvals.append({"id":"approval", "status":"pending", "amount":249.99, "payee":"shop@chargers.example", "tool":"create_order", "risk_score":70, "reason":"Internal rationale"})
    card = run.client_snapshot()["approvals"][0]
    assert card["amount"] == 249.99 and card["payee"] == "shop@chargers.example" and card["tool"] == "create_order"
    assert "risk_score" not in card and "reason" not in card


def test_api_weight_patch_merges_audits_and_filters_reason_codes(tmp_path):
    async def scenario():
        app = create_app(Settings(sqlite_path=str(tmp_path / "api.sqlite3")), paypal=MockPayPal())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch("/api/policies", json={"risk_weights":{"unknown_payee":9}, "allowed_payees":["shop@chargers.example", "trusted.example"], "allowed_payee_names":{"trusted.example":"Trusted shop"}})
                assert response.status_code == 200
                assert response.json()["risk_weights"]["unknown_payee"] == 9
                assert response.json()["risk_weights"]["untrusted_source"] == 40
                row = app.state.audit.report()["rows"][0]
                assert row["after"]["allowed_payee_names"] == {"trusted.example":"Trusted shop"}
                assert row["before"]["risk_weights"]["unknown_payee"] == 18
                assert (await client.patch("/api/policies", json={"allowed_payees":["not a recipient"]})).status_code == 422
                assert (await client.patch("/api/policies", json={"risk_weights":{"untrusted_source":101}})).status_code == 422
                assert (await client.get("/api/audit/flagged?reason_code=unknown_payee")).status_code == 200
    asyncio.run(scenario())


def test_velocity_window_policy_recheck_recounts_and_excludes_own_call():
    async def scenario():
        audit = AuditStore(":memory:")
        policy = Policy.load("policies/default.yaml")
        policy.velocity_call_threshold = 1
        previous = audit.insert(charger(), Verdict(decision="ALLOW", reason="Previous"), "old", True, True, [], "blocked")
        audit.connection.execute("UPDATE audit SET timestamp=datetime('now', '-90 seconds') WHERE id=?", (previous,))
        audit.connection.commit()
        # Use ISO format to match the ledger's lexicographic timestamp ordering.
        from datetime import datetime, timedelta, timezone
        timestamp = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        audit.connection.execute("UPDATE audit SET timestamp=? WHERE id=?", (timestamp, previous))
        audit.connection.commit()
        guard = Firewall(policy, SimpleNamespace(mode="anthropic", judge=AsyncMock()), audit)
        async def emit(event):
            if event["type"] == "decision":
                guard.policy = policy.model_copy(update={"velocity_window_seconds":120})
        await guard.invoke(charger(), Provenance("Buy me a USB-C charger under $40"), AsyncMock(return_value={"id":"MOCK-ORDER", "status":"CREATED"}), emit=emit)
        row = next(row for row in audit.report()["rows"] if row["id"] != previous)
        velocity = next(f for f in row["policy_rechecks"][-1]["factors"] if f["code"] == "velocity")
        assert velocity["points"] == 4 and velocity["reason"].startswith("1 payment calls")
        assert "120 seconds" in velocity["reason"]
        assert audit.recent_count(120, exclude_id=row["id"]) == 1
        audit.close()
    asyncio.run(scenario())


def test_allowed_new_payee_and_unknown_recipient_have_distinct_reason_codes():
    policy = Policy.load("policies/default.yaml")
    provenance = Provenance("Buy me a USB-C charger under $40")
    _, unknown_factors, *_ = score_call(charger(), provenance, policy)
    unknown = next(factor for factor in unknown_factors if factor.code == "unknown_payee")
    assert unknown.name == "Unknown payee" and unknown.points == policy.risk_weights.unknown_payee
    assert unknown.parameter == "risk_weights.unknown_payee"
    allowed_call = charger().model_copy(update={"merchant":"shop@chargers.example"})
    _, allowed_factors, *_ = score_call(allowed_call, provenance, policy, known_payee=False)
    allowed = next(factor for factor in allowed_factors if factor.code == "new_payee")
    assert allowed.name == "New payee" and allowed.points == policy.risk_weights.new_payee
    assert allowed.parameter == "risk_weights.new_payee"
    assert not any(factor.code == "unknown_payee" for factor in allowed_factors)
