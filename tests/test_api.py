import time
from fastapi.testclient import TestClient
from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import RuleJudge


def poll(client,identifier,status="completed"):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        body=client.get(f"/api/runs/{identifier}").json()
        if body["status"]==status:
            return body
        time.sleep(.02)
    raise AssertionError(body)


def test_all_demo_scenarios_and_approval_single_consumption(tmp_path):
    settings=Settings(initial_mode="Enforce",sqlite_path=str(tmp_path/"audit.sqlite3"),demo_mode=True,frontend_dist=str(tmp_path))
    with TestClient(create_app(settings,paypal=MockPayPal(),judge=RuleJudge())) as client:
        assert client.get("/api/health").status_code==200
        assert client.get("/api/config").json()["paypal_mode"]=="MOCK"
        assert client.get("/api/missing").status_code==404
        disabled=client.post("/api/runs",json={"message":"Buy me the cheapest 65W USB-C charger under $40","firewall_enabled":False}).json()["id"]
        off=poll(client,disabled)
        assert len([e for e in off["events"] if e["type"]=="tool_result" and e["status"]=="succeeded"])==2
        enabled=client.post("/api/runs",json={"message":"Buy me the cheapest 65W USB-C charger under $40","firewall_enabled":True}).json()["id"]
        on=poll(client,enabled)
        assert any(e.get("decision")=="BLOCK" and e.get("suspicious_excerpt") for e in on["events"])
        headphones=client.post("/api/runs",json={"message":"Buy me noise-cancelling headphones under $300","firewall_enabled":True}).json()["id"]
        pending=poll(client,headphones,"awaiting_approval")
        assert 60 <= pending["approvals"][0]["risk_score"] <= 84
        identifier=pending["approvals"][0]["id"]
        assert client.post(f"/api/approvals/{identifier}",json={"approved":True}).status_code==200
        assert client.post(f"/api/approvals/{identifier}",json={"approved":True}).status_code==409
        final=poll(client,headphones)
        assert final["approvals"][0]["status"]=="approved"
        audit=client.get("/api/audit").json()
        payments = [r for r in audit["rows"] if r["tool"] in {"create_order", "create_payout"}]
        assert len(payments) == 5
        for run_id, expected_status in [(disabled, "succeeded"), (enabled, "blocked")]:
            order = next(row for row in payments if row["run_id"] == run_id and row["tool"] == "create_order")
            payout = next(row for row in payments if row["run_id"] == run_id and row["tool"] == "create_payout")
            assert order["amount"] == 19.99 and order["computed_decision"] == "ALLOW"
            assert order["call"]["merchant"] == "marketplace.volt.example"
            assert order["status"] == "succeeded"
            assert payout["amount"] == 500 and payout["computed_decision"] == "BLOCK"
            assert payout["status"] == expected_status
            assert payout["display_decision"] == ("Would have blocked" if run_id == disabled else "Blocked")
        assert len([row for row in payments if row["run_id"] == headphones]) == 1
        assert audit["stats"]=={"blocked_amount":500,"allowed_amount":789.97}
        assert all(row["mock"] for row in audit["rows"] if row["tool"] in {"create_order", "create_payout"})


def test_agent_mode_api_validation_and_honest_no_key_fallback(tmp_path):
    settings=Settings(initial_mode="Enforce",sqlite_path=str(tmp_path/"modes.sqlite3"), frontend_dist=str(tmp_path))
    with TestClient(create_app(settings,paypal=MockPayPal(),judge=RuleJudge())) as client:
        config=client.get("/api/config").json()
        assert config["agent_mode"] == "compromised"
        assert config["live_available"] is False
        assert client.post("/api/runs",json={"message":"hello","agent_mode":"unknown"}).status_code == 422
        identifier=client.post("/api/runs",json={"message":"hello","agent_mode":"live"}).json()["id"]
        run=poll(client,identifier)
        assert run["agent_mode"] == "compromised"
        assert any("Live AI agent unavailable" in message["content"] for message in run["messages"])
        assert any("Scripted agent" in message["content"] for message in run["messages"])


def test_api_default_mode_and_live_selection_with_legacy_demo_mode(tmp_path):
    from unittest.mock import AsyncMock
    settings=Settings(agent_provider="anthropic", anthropic_api_key="test-key",demo_mode=True,agent_model="custom-agent",judge_model="custom-judge",sqlite_path=str(tmp_path/"live.sqlite3"),frontend_dist=str(tmp_path))
    app=create_app(settings,paypal=MockPayPal(),judge=RuleJudge())
    app.state.agent.live=AsyncMock()
    with TestClient(app) as client:
        config=client.get("/api/config").json()
        assert config["agent_mode"] == "compromised"
        assert config["live_available"] is True
        assert config["agent_model"] == "custom-agent"
        identifier=client.post("/api/runs",json={"message":"hello","agent_mode":"live"}).json()["id"]
        assert poll(client,identifier)["agent_mode"] == "live"
        app.state.agent.live.assert_awaited_once()



def test_openai_default_live_identity_with_independent_anthropic_judge(tmp_path):
    from unittest.mock import AsyncMock
    settings = Settings(openai_api_key="test-openai", anthropic_api_key="test-anthropic",
        sqlite_path=str(tmp_path/"openai.sqlite3"), frontend_dist=str(tmp_path))
    app = create_app(settings, paypal=MockPayPal())
    app.state.agent.live = AsyncMock()
    with TestClient(app) as client:
        config = client.get("/api/config").json()
        assert config["agent_mode"] == "live" and config["live_available"]
        assert config["agent_provider"] == "openai" and config["agent_model"] == "gpt-4o-mini"
        assert config["judge_mode"] == "anthropic" and config["judge_model"] == "claude-haiku-5-5"
        identifier = client.post("/api/runs", json={"message":"hello"}).json()["id"]
        run = poll(client, identifier)
        assert run["agent_provider"] == "openai" and run["agent_model"] == "gpt-4o-mini"
        assert run["judge_model"] == "claude-haiku-5-5"
        app.state.agent.live.assert_awaited_once()


def test_cancel_pending_approval_is_audited_and_unlocks_new_run(tmp_path):
    from unittest.mock import AsyncMock
    paypal = MockPayPal()
    paypal.execute = AsyncMock(wraps=paypal.execute)
    settings = Settings(initial_mode="Enforce",sqlite_path=str(tmp_path/"cancel.sqlite3"), demo_mode=True)
    with TestClient(create_app(settings, paypal=paypal, judge=RuleJudge())) as client:
        identifier = client.post("/api/runs", json={"message":"Buy me noise-cancelling headphones under $300"}).json()["id"]
        pending = poll(client, identifier, "awaiting_approval")
        approval_id = pending["approvals"][0]["id"]
        response = client.post(f"/api/runs/{identifier}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert response.json()["approvals"][0]["status"] == "cancelled"
        assert client.post(f"/api/approvals/{approval_id}", json={"approved":True}).status_code == 409
        assert client.post(f"/api/runs/{identifier}/cancel").json()["status"] == "cancelled"
        audit = client.get("/api/audit").json()
        assert len(audit["rows"]) == 2
        assert all(row["status"] == "cancelled" and row["decision"] == "BLOCK" for row in audit["rows"])
        assert sum(row["tool"] == "cancel_run" for row in audit["rows"]) == 1
        paypal.execute.assert_not_awaited()
        next_run = client.post("/api/runs", json={"message":"hello"})
        assert next_run.status_code == 202
        completed = poll(client, next_run.json()["id"])
        assert client.post(f"/api/runs/{completed['id']}/cancel").json()["status"] == "completed"
        assert len(client.get("/api/audit").json()["rows"]) == 2


def test_missing_run_get_and_cancel_return_404(tmp_path):
    settings = Settings(initial_mode="Enforce",sqlite_path=str(tmp_path/"missing.sqlite3"))
    with TestClient(create_app(settings, paypal=MockPayPal(), judge=RuleJudge())) as client:
        for response in (client.get("/api/runs/server-restarted"), client.post("/api/runs/server-restarted/cancel")):
            assert response.status_code == 404
            assert response.json()["detail"] == "Run not found"


def test_cancel_during_execution_preserves_unknown_outcome_and_stops_next_payment(tmp_path):
    import asyncio
    import threading
    class SubmittedPayPal(MockPayPal):
        submitted = threading.Event()
        calls = []

        async def execute(self, call, request_id):
            self.calls.append(call)
            self.submitted.set()
            await asyncio.Event().wait()

    paypal = SubmittedPayPal()
    settings = Settings(initial_mode="Enforce",sqlite_path=str(tmp_path/"executing.sqlite3"), demo_mode=True)
    with TestClient(create_app(settings, paypal=paypal, judge=RuleJudge())) as client:
        identifier = client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40", "firewall_enabled":False}).json()["id"]
        assert paypal.submitted.wait(3)
        assert client.post(f"/api/runs/{identifier}/cancel").json()["status"] == "cancelled"
        assert client.get(f"/api/runs/{identifier}").json()["status"] == "cancelled"
        rows = client.get("/api/audit").json()["rows"]
        payment = next(row for row in rows if row["tool"] == "create_order")
        assert payment["status"] == "unknown"
        assert "verify PayPal" in payment["reason"]
        assert len(paypal.calls) == 1
        assert any(row["tool"] == "cancel_run" for row in rows)


def test_cancel_before_payment_and_after_approval_resolution_race(tmp_path):
    import asyncio
    import httpx
    from unittest.mock import AsyncMock
    from airlock.app import ApprovalChoice

    async def scenario():
        settings = Settings(initial_mode="Enforce",sqlite_path=str(tmp_path/"race.sqlite3"), demo_mode=True)
        paypal = MockPayPal()
        paypal.execute = AsyncMock(wraps=paypal.execute)
        app = create_app(settings, paypal=paypal, judge=RuleJudge())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                # Start and immediately cancel before the new agent task is scheduled.
                first = (await client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40"})).json()["id"]
                assert (await client.post(f"/api/runs/{first}/cancel")).json()["status"] == "cancelled"
                await asyncio.sleep(0)
                rows = (await client.get("/api/audit")).json()["rows"]
                assert len(rows) == 1 and rows[0]["tool"] == "cancel_run"
                second = (await client.post("/api/runs", json={"message":"Buy me noise-cancelling headphones under $300"})).json()["id"]
                for _ in range(200):
                    await asyncio.sleep(.01)
                    run = app.state.runs[second]
                    if run.status == "awaiting_approval":
                        break
                assert run.status == "awaiting_approval"
                approval_id = run.approvals[0]["id"]
                resolve = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/approvals/{identifier}")
                cancel = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/runs/{identifier}/cancel")
                # Resolve the future, then cancel before the agent resumes from it.
                await resolve(approval_id, ApprovalChoice(approved=True))
                await cancel(second)
                await asyncio.sleep(0)
                assert run.status == "cancelled" and run.approvals[0]["status"] == "cancelled"
                assert (await client.post(f"/api/approvals/{approval_id}", json={"approved":True})).status_code == 409
                paypal.execute.assert_not_awaited()
                rows = (await client.get("/api/audit")).json()["rows"]
                assert all(row["status"] == "cancelled" for row in rows)
    asyncio.run(scenario())


def test_redesigned_routes_shared_mode_policy_audit_and_wallet(tmp_path):
    from decimal import Decimal
    (tmp_path/"index.html").write_text("<html>Airlock</html>")
    app = create_app(Settings(sqlite_path=str(tmp_path/"redesign.sqlite3"), frontend_dist=str(tmp_path)), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/console"
        for path in ["/client","/console","/docs"]:
            response = client.get(path)
            assert response.status_code == 200
            assert response.text == "<html>Airlock</html>"
        assert client.get("/not-a-page").status_code == 404
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/mode").json() == {"mode":"Monitor"}
        assert client.get("/api/wallet").json()["balance"] == "10000.00"
        first = client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40"}).json()["id"]
        poll(client, first)
        assert client.get("/api/wallet").json()["balance"] == "9480.01"
        assert client.patch("/api/mode",json={"mode":"Enforce"}).json() == {"mode":"Enforce"}
        second = client.post("/api/runs",json={"message":"Buy me a USB-C charger under $40"}).json()["id"]
        poll(client, second)
        assert client.get("/api/wallet").json()["balance"] == "9460.02"
        policy = client.get("/api/policies").json()
        policy["single_transaction_limit"] = "200"
        assert client.patch("/api/policies",json=policy).status_code == 200
        assert Decimal(client.get("/api/policies").json()["single_transaction_limit"]) == 200
        assert client.patch("/api/policies",json={"single_transaction_limit":-1}).status_code == 422
        assert client.patch("/api/mode",json={"mode":"Off"}).status_code == 422
        rows = client.get("/api/audit").json()["rows"]
        assert any(row["tool"] in {"mode_updated", "policy_updated"} for row in rows)


def test_sse_state_initial_snapshot_cross_tab_changes_and_safe_client_projection(tmp_path):
    import asyncio
    import json
    from airlock.store import Run
    from airlock.catalog import INJECTION
    class Connected:
        async def is_disconnected(self):
            return False
    async def scenario():
        app = create_app(Settings(sqlite_path=str(tmp_path/"sse.sqlite3")),paypal=MockPayPal(),judge=RuleJudge())
        async with app.router.lifespan_context(app):
            stream1 = app.state.event_stream(Connected(), "console")
            stream2 = app.state.event_stream(Connected(), "console")
            parse = lambda event:json.loads(event.split("data: ",1)[1].strip())
            initial1 = parse(await anext(stream1))
            initial2 = parse(await anext(stream2))
            assert initial1 == initial2 and initial1["config"]["mode"] == "Monitor"
            from airlock.app import ModeChoice
            change_mode = next(route.endpoint for route in app.routes if getattr(route,"path",None) == "/api/mode" and "PATCH" in route.methods)
            await change_mode(ModeChoice(mode="Enforce"))
            mode1 = parse(await anext(stream1))
            mode2 = parse(await anext(stream2))
            assert mode1 == mode2 and mode1["config"]["mode"] == "Enforce"
            endpoint = next(route.endpoint for route in app.routes if getattr(route,"path",None) == "/api/events")
            response = await endpoint(Connected(), "client")
            assert response.media_type == "text/event-stream" and response.headers["x-accel-buffering"] == "no"
            assert parse(await anext(response.body_iterator))["audit"]["rows"] == []
            await response.body_iterator.aclose()
            run = Run("Buy me a USB-C charger under $40", True)
            app.state.runs[run.id] = run
            await app.state.agent.products(run)
            await app.state.agent.open_product(run, "marketplace-volt-30w")
            await run.emit({"type":"decision","decision":"BLOCK","status":"blocked","risk_score":92,"factors":[{"name":"taint","points":32}],"suspicious_excerpt":INJECTION,"message":"Tool-only instructions"})
            update1 = parse(await anext(stream1))
            update2 = parse(await anext(stream2))
            assert update1 == update2 and update1["runs"][0]["events"][-1]["risk_score"] == 92
            client_state = await app.state.snapshot("client")
            serialized = json.dumps(client_state)
            assert "risk_score" not in serialized and "suspicious_excerpt" not in serialized and INJECTION not in serialized
            assert "agent_provider" not in serialized and "judge_model" not in serialized
            assert client_state["runs"][0]["events"][-1]["message"] == "Payment tool rejected the request"
            await stream1.aclose()
            await stream2.aclose()
    asyncio.run(scenario())


def test_sandbox_reset_requires_confirmation_and_clears_only_local_demo_data(tmp_path):
    from decimal import Decimal
    settings = Settings(sqlite_path=str(tmp_path/"reset.sqlite3"), demo_mode=True)
    app = create_app(settings, paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        identifier = client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40"}).json()["id"]
        poll(client, identifier)
        assert app.state.audit.reserve_total("weekly") == Decimal("519.99")
        assert client.get("/api/wallet").json()["balance"] == "9480.01"
        assert client.patch("/api/policies", json={"daily_limit":"2500"}).status_code == 200
        before_policy = client.get("/api/policies").json()
        app.state.llm_budget.reserve()
        before_model_budget = app.state.llm_budget.snapshot()
        before_rate_limit = {key:list(value) for key,value in app.state.rate_limiter.clients.items()}
        app.state.firewall._reviews["finished-run"] = "cached review"
        before_rows = client.get("/api/audit").json()["rows"]
        for body in [{}, {"confirmed":False}, {"confirmed":1}, {"confirmed":"true"}, {"confirmed":True, "extra":True}]:
            assert client.post("/api/sandbox/reset", json=body).status_code == 422
            assert client.get("/api/audit").json()["rows"] == before_rows
        response = client.post("/api/sandbox/reset", json={"confirmed":True})
        assert response.status_code == 200
        assert "not reversed" in response.json()["message"]
        assert response.json()["wallet"]["balance"] == "10000.00"
        assert client.get("/api/audit").json() == {"rows":[],"stats":{"blocked_amount":0,"allowed_amount":0}}
        assert client.get(f"/api/runs/{identifier}").status_code == 404
        assert app.state.audit.reserve_total("weekly") == 0
        assert app.state.runs == {} and app.state.firewall._reviews == {}
        assert client.get("/api/policies").json() == before_policy
        assert client.get("/api/mode").json()["mode"] == "Monitor"
        assert app.state.llm_budget.snapshot() == before_model_budget
        assert {key:list(value) for key,value in app.state.rate_limiter.clients.items()} == before_rate_limit
        # Reset is idempotent and does not recreate an audit row.
        assert client.post("/api/sandbox/reset", json={"confirmed":True}).status_code == 200
        assert client.get("/api/audit").json()["rows"] == []


def test_sandbox_reset_rejects_running_and_pending_approval_runs(tmp_path):
    import asyncio
    import threading
    class WaitingPayPal(MockPayPal):
        submitted = threading.Event()
        async def execute(self, call, request_id):
            self.submitted.set()
            await asyncio.Event().wait()
    paypal = WaitingPayPal()
    app = create_app(Settings(sqlite_path=str(tmp_path/"reset-active.sqlite3"), demo_mode=True), paypal=paypal, judge=RuleJudge())
    with TestClient(app) as client:
        identifier = client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40"}).json()["id"]
        assert paypal.submitted.wait(3)
        assert client.post("/api/sandbox/reset", json={"confirmed":True}).status_code == 409
        assert client.get("/api/audit").json()["rows"]
        client.post(f"/api/runs/{identifier}/cancel")
        client.patch("/api/mode", json={"mode":"Enforce"})
        identifier = client.post("/api/runs", json={"message":"Buy me noise-cancelling headphones under $300"}).json()["id"]
        run = poll(client, identifier, "awaiting_approval")
        assert client.post("/api/sandbox/reset", json={"confirmed":True}).status_code == 409
        assert client.post(f"/api/approvals/{run['approvals'][0]['id']}", json={"approved":False}).status_code == 200
        poll(client, identifier)
        assert client.post("/api/sandbox/reset", json={"confirmed":True}).status_code == 200
        assert client.post(f"/api/approvals/{run['approvals'][0]['id']}", json={"approved":True}).status_code == 409


def test_sandbox_reset_sse_updates_console_and_client_snapshots(tmp_path):
    import asyncio
    import json
    from airlock.app import ResetSandboxData
    from airlock.store import Run
    from firewall import Decision, Provenance, ToolCall, Verdict
    class Connected:
        async def is_disconnected(self):
            return False
    async def scenario():
        app = create_app(Settings(sqlite_path=str(tmp_path/"reset-sse.sqlite3")), paypal=MockPayPal(), judge=RuleJudge())
        async with app.router.lifespan_context(app):
            run = Run("Buy me a USB-C charger under $40", False)
            run.status = "completed"
            app.state.runs[run.id] = run
            app.state.audit.insert(ToolCall(tool="create_order", amount="19.99", payee="shop@chargers.example"), Verdict(decision=Decision.ALLOW, reason="Requested purchase", mode="Monitor"), run.id, False, True, Provenance(run.message).serialize(), "succeeded")
            console = app.state.event_stream(Connected(), "console")
            client = app.state.event_stream(Connected(), "client")
            parse = lambda event:json.loads(event.split("data: ",1)[1].strip())
            for stream in [console, client]:
                initial = parse(await anext(stream))
                assert initial["runs"] and initial["wallet"]["balance"] == "9980.01"
            reset = next(route.endpoint for route in app.routes if getattr(route,"path",None) == "/api/sandbox/reset")
            await reset(ResetSandboxData(confirmed=True))
            for stream in [console, client]:
                snapshot = parse(await anext(stream))
                assert snapshot["runs"] == [] and snapshot["audit"]["rows"] == []
                assert snapshot["wallet"]["balance"] == "10000.00"
                await stream.aclose()
    asyncio.run(scenario())


def test_sandbox_reset_rejects_unfinished_task_even_with_terminal_run_status(tmp_path):
    import asyncio
    import threading
    app = create_app(Settings(sqlite_path=str(tmp_path/"reset-task.sqlite3")), paypal=MockPayPal(), judge=RuleJudge())
    started = threading.Event()
    async def unfinished(run):
        run.status = "completed"
        started.set()
        await asyncio.Event().wait()
    app.state.agent.run = unfinished
    with TestClient(app) as client:
        client.post("/api/runs", json={"message":"hello"})
        assert started.wait(3)
        assert client.post("/api/sandbox/reset", json={"confirmed":True}).status_code == 409
        assert app.state.runs


def test_channel3_offer_order_waits_for_approval_then_creates_sandbox_order(tmp_path):
    import json
    import httpx
    from unittest.mock import AsyncMock
    from airlock.catalog import Channel3Catalog, INJECTION

    requests = []
    def search(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"products":[{"id":"sony-live-offer", "title":"Sony Noise-Cancelling Headphones", "description":"Wireless over-ear headphones", "category":{"slug":"electronics"}, "offers":[{"domain":"sony.com", "url":"https://electronics.sony.com/audio/headphones/p/live-offer", "price":{"price":249.99,"currency":"USD"}}]}]})
    catalog = Channel3Catalog("test-channel3-key", transport=httpx.MockTransport(search))
    paypal = MockPayPal()
    paypal.execute = AsyncMock(wraps=paypal.execute)
    settings = Settings(initial_mode="Enforce", sqlite_path=str(tmp_path/"channel3-approval.sqlite3"), demo_mode=True)
    app = create_app(settings, paypal=paypal, judge=RuleJudge(), catalog=catalog)
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"message":"Buy me noise-cancelling headphones under $300"}).json()["id"]
        pending = poll(client, run_id, "awaiting_approval")
        paypal.execute.assert_not_awaited()
        assert len(requests) == 1 and requests[0]["filters"]["price"]["max_price"] == 300
        product = next(event for event in pending["events"] if event["type"] == "product_page")["product"]
        assert product["id"] == "sony-live-offer" and product["url"].startswith("https://electronics.sony.com/")
        identifier = pending["approvals"][0]["id"]
        event = client.get(f"/api/audit/{identifier}").json()
        assert event["call"]["tool"] == "create_order" and event["call"]["merchant"] == "sony.com"
        assert event["call"]["payee"] == settings.shop_email
        assert event["computed_decision"] == "REQUIRE_APPROVAL" and 60 <= event["risk_score"] < 85
        assert client.post(f"/api/approvals/{identifier}", json={"approved":True}).status_code == 200
        poll(client, run_id)
        paypal.execute.assert_awaited_once()
        assert client.get(f"/api/audit/{identifier}").json()["paypal_status"] == "CREATED"
        assert client.post(f"/api/approvals/{identifier}", json={"approved":True}).status_code == 409
        storefront = client.get("/api/store/volt-65w").json()
        assert storefront["url"] == "/store/volt-65w" and INJECTION in storefront["page_text"]


def test_partial_policy_toggles_preserve_other_controls_and_currency_invariant(tmp_path):
    app = create_app(Settings(sqlite_path=str(tmp_path/"partial-policy.sqlite3")), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app) as client:
        first = client.patch("/api/policies", json={"enabled":{"allowlist":False}})
        assert first.status_code == 200
        second = client.patch("/api/policies", json={"enabled":{"categories":False}})
        assert second.status_code == 200
        enabled = second.json()["enabled"]
        assert enabled["allowlist"] is False and enabled["categories"] is False
        assert enabled["single_transaction"] is True
        assert client.patch("/api/policies", json={"currency":"EUR"}).status_code == 422
        assert client.patch("/api/policies", json={"daily_limit":"1e100"}).status_code == 422
        assert client.get("/api/policies").json()["currency"] == "USD"
        assert client.get("/api/policies").json()["daily_limit"] == "2000"


def test_configuration_does_not_change_when_audit_write_fails(tmp_path):
    import sqlite3
    from unittest.mock import Mock

    app = create_app(Settings(initial_mode="Enforce", sqlite_path=str(tmp_path/"audit-failure.sqlite3")), paypal=MockPayPal(), judge=RuleJudge())
    with TestClient(app, raise_server_exceptions=False) as client:
        original_policy = client.get("/api/policies").json()
        app.state.audit.record_config_change = Mock(side_effect=sqlite3.OperationalError("Audit unavailable"))
        assert client.patch("/api/mode", json={"mode":"Monitor"}).status_code == 500
        assert client.get("/api/mode").json()["mode"] == "Enforce"
        assert client.patch("/api/policies", json={"daily_limit":"100"}).status_code == 500
        assert client.get("/api/policies").json() == original_policy
        assert client.post("/api/runs", json={"message":"Buy me a USB-C charger under $40", "firewall_enabled":False}).status_code == 500
        assert client.get("/api/mode").json()["mode"] == "Enforce"
        assert not app.state.runs


def test_approval_audit_failure_keeps_payment_pending_until_successful_retry(tmp_path):
    import sqlite3
    from unittest.mock import AsyncMock, Mock

    paypal = MockPayPal()
    paypal.execute = AsyncMock(wraps=paypal.execute)
    app = create_app(Settings(initial_mode="Enforce", demo_mode=True, sqlite_path=str(tmp_path/"approval-audit-failure.sqlite3")), paypal=paypal, judge=RuleJudge())
    with TestClient(app, raise_server_exceptions=False) as client:
        run_id = client.post("/api/runs", json={"message":"Buy me noise-cancelling headphones under $300"}).json()["id"]
        run = poll(client, run_id, "awaiting_approval")
        identifier = run["approvals"][0]["id"]
        original_record = app.state.audit.record_approval
        app.state.audit.record_approval = Mock(side_effect=sqlite3.OperationalError("Audit write failed"))
        assert client.post(f"/api/approvals/{identifier}", json={"approved":True}).status_code == 500
        time.sleep(.03)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
        event = client.get(f"/api/audit/{identifier}").json()
        assert event["status"] == "awaiting_approval"
        assert [entry["action"] for entry in event["approval_history"]] == ["requested"]
        paypal.execute.assert_not_awaited()
        app.state.audit.record_approval = original_record
        assert client.post(f"/api/approvals/{identifier}", json={"approved":True}).status_code == 200
        poll(client, run_id)
        assert client.post(f"/api/approvals/{identifier}", json={"approved":True}).status_code == 409
        paypal.execute.assert_awaited_once()
        event = client.get(f"/api/audit/{identifier}").json()
        assert [entry["action"] for entry in event["approval_history"]] == ["requested", "approved"]


def test_cancellation_audit_failure_still_stops_agent_before_payment(tmp_path):
    import sqlite3
    from unittest.mock import AsyncMock, Mock

    paypal = MockPayPal()
    paypal.execute = AsyncMock(wraps=paypal.execute)
    app = create_app(Settings(initial_mode="Enforce", demo_mode=True, sqlite_path=str(tmp_path/"cancel-audit-failure.sqlite3")), paypal=paypal, judge=RuleJudge())
    with TestClient(app, raise_server_exceptions=False) as client:
        run_id = client.post("/api/runs", json={"message":"Buy me the cheapest 65W USB-C charger under $40"}).json()["id"]
        for _ in range(100):
            run = client.get(f"/api/runs/{run_id}").json()
            if any(event["type"] == "product_page" for event in run["events"]):
                break
            time.sleep(.001)
        assert run["status"] == "running"
        paypal.execute.assert_not_awaited()
        app.state.audit.record_run_cancellation = Mock(side_effect=sqlite3.OperationalError("Audit write failed"))
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 500
        time.sleep(.2)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "cancelled"
        paypal.execute.assert_not_awaited()
        assert not client.get("/api/audit").json()["rows"]
