import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import AuditStore, Firewall, Policy, Provenance, RuleJudge, ToolCall, Verdict
from firewall.policy import POLICY_IDS


def poll(client, identifier, status="completed"):
    for _ in range(200):
        run = client.get(f"/api/runs/{identifier}").json()
        if run["status"] == status:
            return run
        assert run["status"] != "failed", run
        time.sleep(.01)
    raise AssertionError(run)


def test_event_detail_records_call_five_policy_snapshot_review_evidence_and_run_links(tmp_path):
    app = create_app(Settings(sqlite_path=str(tmp_path/"audit.sqlite3"), demo_mode=True), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40"}).json()["id"]
        poll(client, run)
        rows = client.get("/api/audit").json()["rows"]
        payout = next(row for row in rows if row["tool"] == "create_payout")
        event = client.get(f"/api/audit/{payout['id']}").json()
        assert event["call"]["amount"] == "500"
        assert event["call"]["description"] and event["call"]["category"] == ""
        order = next(row for row in rows if row["tool"] == "create_order")
        assert order["call"]["category"] and order["call"]["description"]
        assert event["computed_decision"] == "BLOCK"
        assert event["display_decision"] == "Would have blocked"
        assert event["enforced_decision"] == "ALLOW" and event["status"] == "succeeded"
        assert [check["id"] for check in event["policy_checks"]] == list(POLICY_IDS)
        checks = {check["id"]:check for check in event["policy_checks"]}
        assert checks["allowlist"]["outcome"] == "fail" and "adds risk" in checks["allowlist"]["reason"]
        assert checks["single_transaction"]["outcome"] == "pass"
        assert checks["risk_threshold"]["outcome"] == "fail"
        review = event["ai_review_detail"]
        assert review["invoked"] and not review["model_invoked"] and review["reviewer"] == "rules"
        assert review["status"] == "completed" and review["decision"] == "BLOCK"
        assert review["reason"] and review["latency_ms"] >= 0
        source = next(source for source in event["provenance"] if source["id"] == event["source"])
        assert event["suspicious_excerpt"] in source["text"]
        assert event["execution_result"]["id"] == event["paypal_id"]
        assert "MOCK" in event["paypal_id"] and event["paypal_status"] == "PENDING"
        assert event["latency"]["execution_ms"] >= 0 and event["latency"]["end_to_end_ms"] >= event["latency"]["total_ms"]
        assert len(event["run_events"]) == 1 and event["run_events"][0]["tool"] == "create_order"
        client.patch("/api/policies", json={"single_transaction_limit":"100"})
        unchanged = client.get(f"/api/audit/{payout['id']}").json()
        assert unchanged["policy_checks"] == event["policy_checks"]
        assert unchanged["policy_snapshot"]["single_transaction_limit"] == "1000"
        assert client.get("/api/audit/does-not-exist").status_code == 404


@pytest.mark.parametrize("approved", [True, False])
def test_flagged_filters_keep_resolved_approvals_and_approval_history(tmp_path, approved):
    app = create_app(Settings(initial_mode="Enforce", sqlite_path=str(tmp_path/"approval.sqlite3"), demo_mode=True), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"message":"Buy me noise-cancelling headphones under $300"}).json()["id"]
        pending = poll(client, run, "awaiting_approval")
        identifier = pending["approvals"][0]["id"]
        assert client.get("/api/audit/flagged?type=REQUIRE_APPROVAL").json()["stats"]["total_amount"] == "249.99"
        assert client.post(f"/api/approvals/{identifier}", json={"approved":approved}).status_code == 200
        poll(client, run)
        event = client.get(f"/api/audit/{identifier}").json()
        assert event["computed_decision"] == "REQUIRE_APPROVAL"
        assert event["status"] == ("succeeded" if approved else "denied")
        assert [entry["action"] for entry in event["approval_history"]] == ["requested", "approved" if approved else "denied"]
        assert event["approval_history"][-1]["actor"] == "Sandbox console operator (unauthenticated)"
        assert all(entry["timestamp"] for entry in event["approval_history"])
        assert event["latency"]["approval_ms"] >= 0
        result = client.get("/api/audit/flagged?type=REQUIRE_APPROVAL&policy=risk_threshold").json()
        assert [row["id"] for row in result["rows"]] == [identifier]
        assert result["stats"]["totals_by_currency"] == {"USD":"249.99"}
        assert not client.get("/api/audit/flagged?type=BLOCK").json()["rows"]
        assert not client.get("/api/audit/flagged?policy=categories").json()["rows"]
        assert client.get("/api/audit/flagged?type=ALLOW").status_code == 422
        assert client.get("/api/audit/flagged?policy=unknown").status_code == 422
        if not approved:
            assert event["execution_result"] is None and event["paypal_id"] is None
            assert event["decision"] == "BLOCK" and event["display_decision"] == "Blocked"
            assert event["latency"]["end_to_end_ms"] >= event["latency"]["approval_ms"]


def test_flagged_includes_monitor_blocks_enforce_blocks_and_exact_currency_totals(tmp_path):
    app = create_app(Settings(sqlite_path=str(tmp_path/"flagged.sqlite3")), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        for index, (currency, amount, mode, decision) in enumerate([
            ("USD","0.10","Monitor","BLOCK"), ("USD","0.20","Enforce","BLOCK"),
            ("EUR","1.11","Enforce","REQUIRE_APPROVAL"), ("USD","99","Monitor","ALLOW")]):
            app.state.audit.insert(ToolCall(tool="create_order", amount=amount, currency=currency, payee="shop"),
                Verdict(decision=decision, reason="Test", mode=mode, policy="categories"), f"run-{index}", mode == "Enforce", True, [], "blocked" if mode == "Enforce" else "succeeded")
        report = client.get("/api/audit/flagged").json()
        assert report["stats"] == {"count":3,"total_amount":"0.30","currency":"USD","totals_by_currency":{"EUR":"1.11","USD":"0.30"}}
        assert [row["run_id"] for row in report["rows"]] == ["run-2","run-1","run-0"]
        assert len(client.get("/api/audit/flagged?policy=categories").json()["rows"]) == 3


def test_legacy_detail_is_unknown_and_restart_preserves_approval_computation(tmp_path):
    path = str(tmp_path/"legacy.sqlite3")
    store = AuditStore(path)
    legacy = store.insert(ToolCall(tool="create_order", amount="1", payee="shop"), Verdict(decision="ALLOW", reason="Old record"), "old", True, True, [], "succeeded")
    store.connection.execute("UPDATE audit SET metadata='{}' WHERE id=?", (legacy,))
    pending = store.insert(ToolCall(tool="create_order", amount="249.99", payee="shop", merchant="sony.com", category="electronics", description="headphones"), Verdict(decision="REQUIRE_APPROVAL", reason="Review"), "restart", True, True, [], "awaiting_approval")
    store.connection.commit()
    store.close()
    app = create_app(Settings(sqlite_path=path), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        event = client.get(f"/api/audit/{legacy}").json()
        assert event["call"] is None and event["risk_score"] is None and event["latency"] is None
        assert event["ai_review_detail"] is None and event["policy_snapshot"] is None
        assert all(check["outcome"] == "unknown" for check in event["policy_checks"])
        flagged = client.get("/api/audit/flagged?type=REQUIRE_APPROVAL").json()["rows"]
        assert flagged[0]["id"] == pending and flagged[0]["status"] == "cancelled"
        assert flagged[0]["approval_history"][-1]["actor"] == "System (server restart)"


def test_spa_nested_routes_return_app_and_do_not_swallow_missing_api_assets(tmp_path):
    (tmp_path/"index.html").write_text("<html>Airlock application</html>")
    app = create_app(Settings(sqlite_path=str(tmp_path/"spa.sqlite3"), frontend_dist=str(tmp_path)), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        for route in ["/console/events/event-id", "/console/flagged", "/store/volt-65w"]:
            response = client.get(route)
            assert response.status_code == 200 and response.text == "<html>Airlock application</html>"
        assert client.get("/api/audit/missing").status_code == 404
        assert client.get("/console/unknown").status_code == 404
        assert client.get("/assets/unknown.js").status_code == 404


def test_policy_snapshot_stays_fixed_during_review_and_post_approval_recheck_is_separate():
    async def scenario():
        audit = AuditStore(":memory:")
        policy = Policy(allowed_payees=["shop"])
        judge = type("Judge", (), {"mode":"rules", "judge":AsyncMock(return_value=Verdict(decision="ALLOW", reason="Authorized"))})()
        firewall = Firewall(policy, judge, audit)
        async def approve(*args):
            firewall.policy.daily_limit = Decimal("100")
            return True
        execute = AsyncMock()
        await firewall.invoke(ToolCall(tool="create_order", amount="249.99", payee="shop", merchant="sony.com", category="electronics", description="headphones"), Provenance("Buy me noise-cancelling headphones under $300"), execute, request_approval=approve)
        event = audit.report()["rows"][0]
        assert event["policy_snapshot"]["daily_limit"] == "2000"
        assert event["policy_checks"][1]["outcome"] == "pass"
        assert event["policy_rechecks"][-1]["stage"] == "after_approval"
        assert event["policy_rechecks"][-1]["policy_checks"][1]["outcome"] == "fail"
        assert event["decision"] == "BLOCK" and event["computed_decision"] == "REQUIRE_APPROVAL"
        assert event["latency"]["end_to_end_ms"] >= event["latency"]["approval_ms"]
        # The initial primary policy remains risk_threshold; filters must also
        # include the budget failure discovered by the approval recheck.
        assert event["policy"] == "risk_threshold"
        assert audit.flagged(policy="period_budget")["rows"][0]["id"] == event["id"]
        execute.assert_not_awaited()
        audit.close()
    asyncio.run(scenario())


def test_review_detail_distinguishes_skipped_cached_ai_and_failed_review():
    async def scenario():
        audit = AuditStore(":memory:")
        policy = Policy(allowed_payees=["shop", "shop"])
        judge = type("Judge", (), {"mode":"anthropic", "judge":AsyncMock(return_value=Verdict(decision="ALLOW", reason="Authorized"))})()
        firewall = Firewall(policy, judge, audit)
        executor = AsyncMock(return_value={"id":"MOCK-ID", "status":"PENDING"})
        purchase = ToolCall(tool="create_order", amount="1", payee="shop", description="charger")
        await firewall.invoke(purchase, Provenance("Buy me a USB-C charger under $40"), executor, run_id="low-risk")
        skipped = audit.report()["rows"][0]["ai_review_detail"]
        assert skipped["status"] == "skipped" and not skipped["invoked"] and not skipped["model_invoked"]
        call = ToolCall(tool="create_order", amount="249.99", payee="shop", merchant="sony.com", category="electronics", description="headphones")
        provenance = Provenance("Buy me noise-cancelling headphones under $300")
        for _ in range(2):
            await firewall.invoke(call, provenance, executor, run_id="cached", mode="Monitor")
        rows = audit.report()["rows"]
        reviews = [row["ai_review_detail"] for row in rows if row["run_id"] == "cached"]
        assert reviews[0]["status"] == "cached" and reviews[0]["cached"] and not reviews[0]["model_invoked"]
        assert reviews[1]["status"] == "completed" and reviews[1]["model_invoked"]
        judge.judge.side_effect = TimeoutError()
        await firewall.invoke(call, provenance, executor, run_id="failed", mode="Monitor")
        failed = audit.report()["rows"][0]
        assert failed["computed_decision"] == "REQUIRE_APPROVAL"
        assert failed["ai_review_detail"]["status"] == "unavailable" and failed["ai_review_detail"]["model_invoked"]
        audit.close()
    asyncio.run(scenario())


def test_review_await_keeps_original_policy_snapshot_and_records_current_policy_recheck():
    async def scenario():
        audit = AuditStore(":memory:")
        policy = Policy(allowed_payees=["shop"])
        async def review(call, provenance):
            firewall.policy = policy.model_copy(update={"single_transaction_limit":Decimal("50")})
            await asyncio.sleep(0)
            return Verdict(decision="ALLOW", reason="Authorized")
        judge = type("Judge", (), {"mode":"rules", "judge":staticmethod(review)})()
        firewall = Firewall(policy, judge, audit)
        executor = AsyncMock()
        result = await firewall.invoke(ToolCall(tool="create_order", amount="249.99", payee="shop", merchant="sony.com", category="electronics", description="headphones"), Provenance("Buy me noise-cancelling headphones under $300"), executor)
        assert result["decision"] == "BLOCK"
        event = audit.report()["rows"][0]
        assert event["latency"]["end_to_end_ms"] >= event["latency"]["total_ms"]
        assert event["policy_snapshot"]["single_transaction_limit"] == "1000"
        assert event["policy_checks"][0]["outcome"] == "pass"
        assert event["policy_rechecks"][0]["stage"] == "before_execution"
        assert event["policy_rechecks"][0]["policy_snapshot"]["single_transaction_limit"] == "50"
        assert event["policy_rechecks"][0]["policy_checks"][0]["outcome"] == "fail"
        executor.assert_not_awaited()
        audit.close()
    asyncio.run(scenario())


def test_cancelled_approval_records_wait_and_total_latency_without_execution():
    async def scenario():
        audit = AuditStore(":memory:")
        policy = Policy(allowed_payees=["shop"])
        judge = type("Judge", (), {"mode":"rules", "judge":AsyncMock(return_value=Verdict(decision="ALLOW", reason="Authorized"))})()
        firewall = Firewall(policy, judge, audit)
        future = asyncio.get_running_loop().create_future()
        executor = AsyncMock()
        task = asyncio.create_task(firewall.invoke(ToolCall(tool="create_order", amount="249.99", payee="shop", merchant="sony.com", category="electronics", description="headphones"), Provenance("Buy me noise-cancelling headphones under $300"), executor, request_approval=lambda *args:future))
        await asyncio.sleep(0)
        assert audit.report()["rows"][0]["status"] == "awaiting_approval"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        event = audit.report()["rows"][0]
        assert event["status"] == "cancelled"
        assert event["approval_history"][-1]["action"] == "cancelled"
        assert event["latency"]["end_to_end_ms"] >= event["latency"]["approval_ms"] >= 0
        assert "execution_ms" not in event["latency"]
        executor.assert_not_awaited()
        audit.close()
    asyncio.run(scenario())


def test_threshold_change_during_judge_review_applies_to_decision_but_preserves_initial_checks():
    async def scenario():
        audit = AuditStore(":memory:")
        policy = Policy(allowed_payees=["shop"])
        async def review(call, provenance):
            await asyncio.sleep(0)
            firewall.policy = policy.model_copy(update={"risk_approval_threshold":40})
            return Verdict(decision="ALLOW", reason="Authorized")
        judge = type("Judge", (), {"mode":"rules", "judge":staticmethod(review)})()
        firewall = Firewall(policy, judge, audit)
        executor = AsyncMock()
        approval = AsyncMock(return_value=False)
        result = await firewall.invoke(ToolCall(tool="create_order", amount="20", payee="shop", merchant="sony.com", category="electronics", description="headphones"), Provenance("Buy me headphones under $40"), executor, request_approval=approval)
        assert result["computed_decision"] == "REQUIRE_APPROVAL"
        approval.assert_awaited_once()
        executor.assert_not_awaited()
        event = audit.report()["rows"][0]
        assert event["risk_score"] == 50
        assert event["policy_snapshot"]["risk_approval_threshold"] == 60
        assert event["policy_checks"][4]["outcome"] == "pass"
        recheck = event["policy_rechecks"][0]
        assert recheck["policy_snapshot"]["risk_approval_threshold"] == 40
        assert recheck["policy_checks"][4]["outcome"] == "fail"
        audit.close()
    asyncio.run(scenario())
