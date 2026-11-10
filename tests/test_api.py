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
        assert any("Live openai agent unavailable" in message["content"] for message in run["messages"])
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
