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
    settings=Settings(sqlite_path=str(tmp_path/"audit.sqlite3"),demo_mode=True,frontend_dist=str(tmp_path))
    with TestClient(create_app(settings,paypal=MockPayPal(),judge=RuleJudge())) as client:
        assert client.get("/api/health").status_code==200
        assert client.get("/api/config").json()["paypal_mode"]=="MOCK"
        assert client.get("/api/missing").status_code==404
        disabled=client.post("/api/runs",json={"message":"Buy me a USB-C charger under $40","firewall_enabled":False}).json()["id"]
        off=poll(client,disabled)
        assert len([e for e in off["events"] if e["type"]=="tool_result" and e["status"]=="succeeded"])==2
        enabled=client.post("/api/runs",json={"message":"Buy me a USB-C charger under $40","firewall_enabled":True}).json()["id"]
        on=poll(client,enabled)
        assert any(e.get("decision")=="BLOCK" and e.get("suspicious_excerpt") for e in on["events"])
        contractor=client.post("/api/runs",json={"message":"Also pay my contractor $120 invoice","firewall_enabled":True}).json()["id"]
        pending=poll(client,contractor,"awaiting_approval")
        identifier=pending["approvals"][0]["id"]
        assert client.post(f"/api/approvals/{identifier}",json={"approved":True}).status_code==200
        assert client.post(f"/api/approvals/{identifier}",json={"approved":True}).status_code==409
        final=poll(client,contractor)
        assert final["approvals"][0]["status"]=="approved"
        audit=client.get("/api/audit").json()
        assert len(audit["rows"])==5
        assert audit["stats"]=={"blocked_amount":500,"allowed_amount":678}
        assert all(row["mock"] for row in audit["rows"])


def test_agent_mode_api_validation_and_honest_no_key_fallback(tmp_path):
    settings=Settings(sqlite_path=str(tmp_path/"modes.sqlite3"), frontend_dist=str(tmp_path))
    with TestClient(create_app(settings,paypal=MockPayPal(),judge=RuleJudge())) as client:
        config=client.get("/api/config").json()
        assert config["agent_mode"] == "compromised"
        assert config["live_available"] is False
        assert client.post("/api/runs",json={"message":"hello","agent_mode":"unknown"}).status_code == 422
        identifier=client.post("/api/runs",json={"message":"hello","agent_mode":"live"}).json()["id"]
        run=poll(client,identifier)
        assert run["agent_mode"] == "compromised"
        assert any("Live AI agent unavailable" in message["content"] for message in run["messages"])
        assert any("Simulated compromised agent" in message["content"] for message in run["messages"])


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
    settings = Settings(sqlite_path=str(tmp_path/"cancel.sqlite3"), demo_mode=True)
    with TestClient(create_app(settings, paypal=paypal, judge=RuleJudge())) as client:
        identifier = client.post("/api/runs", json={"message":"Also pay my contractor $120 invoice"}).json()["id"]
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
    settings = Settings(sqlite_path=str(tmp_path/"missing.sqlite3"))
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
    settings = Settings(sqlite_path=str(tmp_path/"executing.sqlite3"), demo_mode=True)
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
        settings = Settings(sqlite_path=str(tmp_path/"race.sqlite3"), demo_mode=True)
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
                second = (await client.post("/api/runs", json={"message":"Also pay my contractor $120 invoice"})).json()["id"]
                for _ in range(100):
                    await asyncio.sleep(.001)
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
