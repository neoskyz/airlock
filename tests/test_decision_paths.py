"""Only gray-zone decisions consume model calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from firewall import AuditStore, Firewall, Policy, Provenance, ToolCall, Verdict


def test_configured_rule_block_threshold_also_short_circuits_intent_review():
    async def scenario():
        judge = SimpleNamespace(mode="anthropic", judge=AsyncMock())
        audit = AuditStore(":memory:")
        firewall = Firewall(Policy(allowed_payees=["shop@chargers.example"], risk_approval_threshold=30, risk_block_threshold=40), judge, audit)
        call = ToolCall(tool="create_order", amount="249.99", payee="shop@chargers.example", merchant="sony.com", category="electronics")
        result = await firewall.invoke(call, Provenance("Buy me noise-cancelling headphones under $300"), AsyncMock())
        assert result["status"] == "blocked"
        judge.judge.assert_not_awaited()
        assert audit.report()["rows"][0]["ai_review_detail"]["model_invoked"] is False
        audit.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("score,tainted,reviewed", [(29,False,False),(29,True,False),(30,False,True),(84,False,True),(85,False,False),(85,True,False)])
def test_only_gray_boundary_scores_invoke_synchronous_review(monkeypatch, score, tainted, reviewed):
    async def scenario():
        from firewall.models import RiskFactor
        monkeypatch.setattr("firewall.engine.score_call", lambda *args, **kwargs: (score, [RiskFactor(name="Routing fixture", points=score, reason="Fixed boundary score")], tainted, "", ""))
        judge = SimpleNamespace(mode="anthropic", judge=AsyncMock(return_value=Verdict(decision="ALLOW", reason="Matches user")))
        audit = AuditStore(":memory:")
        firewall = Firewall(Policy(allowed_payees=["shop@chargers.example"]), judge, audit)
        call = ToolCall(tool="create_order", amount=29, payee="shop@chargers.example", category="electronics")
        execute = AsyncMock(return_value={"id":"MOCK-ORDER", "status":"CREATED"})
        result = await firewall.invoke(call, Provenance("Buy me a USB-C charger under $40"), execute, request_approval=AsyncMock(return_value=False))
        assert judge.judge.await_count == int(reviewed)
        row = audit.report()["rows"][0]
        assert row["ai_review_detail"]["model_invoked"] is reviewed
        assert row["computed_decision"] == ("ALLOW" if score < 60 else "REQUIRE_APPROVAL" if score < 85 else "BLOCK")
        assert row["latency"]["decision_ms"] is not None
        audit.close()
    asyncio.run(scenario())
