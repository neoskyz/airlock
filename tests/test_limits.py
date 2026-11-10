import asyncio
from decimal import Decimal
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest
from airlock.agent import DemoAgent
from airlock.app import create_app
from airlock.config import Settings
from airlock.limits import RunRateLimiter
from airlock.paypal import MockPayPal
from airlock.store import ApprovalManager, Run
from firewall import AuditStore, Firewall, Policy, Provenance, RuleJudge, ToolCall, build_judge
from firewall.limits import DailyLLMBudget
from test_api import poll


def test_sliding_windows_and_inactive_client_cleanup():
    clock = [0.0]
    limiter = RunRateLimiter(2, 3, clock=lambda: clock[0])
    assert limiter.check("a") is None
    assert limiter.check("a") is None
    assert limiter.check("a") == 60
    assert limiter.check("b") is None
    clock[0] = 60
    assert limiter.check("a") is None
    assert limiter.check("a") == 3540
    clock[0] = 3600
    assert limiter.check("a") is None
    assert "b" not in limiter.clients


def test_forwarded_first_ip_minute_and_hour_api_limits(tmp_path):
    app = create_app(Settings(sqlite_path=str(tmp_path/"audit.sqlite3"),
        run_rate_limit_per_minute=1, run_rate_limit_per_hour=2), paypal=MockPayPal(), judge=RuleJudge())
    clock = [0.0]
    app.state.rate_limiter.clock = lambda: clock[0]
    with TestClient(app) as client:
        def start(chain):
            return client.post("/api/runs", json={"message":"hello"}, headers={"X-Forwarded-For":chain})
        poll(client, start("203.0.113.1, 10.0.0.1").json()["id"])
        denied = start("203.0.113.1, 10.0.0.2")
        assert denied.status_code == 429
        assert denied.headers["retry-after"] == "60"
        assert "Please try again" in denied.json()["detail"]
        poll(client, start("203.0.113.2, 10.0.0.1").json()["id"])
        clock[0] = 60
        poll(client, start("203.0.113.1").json()["id"])
        assert start("203.0.113.1").headers["retry-after"] == "3540"
        clock[0] = 3600
        assert start("203.0.113.1").status_code == 202


def test_budget_atomic_reservations_and_utc_day_reset():
    today = [date(2026, 11, 9)]
    budget = DailyLLMBudget(5, clock=lambda: today[0])
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(lambda _: budget.reserve(), range(30))) == 5
    assert budget.exhausted
    today[0] = date(2026, 11, 10)
    assert budget.snapshot() == {"llm_calls_used":0,"llm_call_limit":5,"llm_degraded":False}
    assert budget.reserve()


def test_failed_judge_attempt_consumes_budget_and_only_quota_uses_rules():
    async def scenario():
        budget = DailyLLMBudget(1)
        create = AsyncMock(side_effect=TimeoutError())
        judge = build_judge("test-key", SimpleNamespace(messages=SimpleNamespace(create=create)), budget=budget)
        call = ToolCall(tool="create_order", amount=29, payee="shop@chargers.example", description="charger")
        provenance = Provenance("Buy me a USB-C charger under $40")
        assert (await judge.judge(call, provenance)).decision == "REQUIRE_APPROVAL"
        assert budget.snapshot()["llm_calls_used"] == 1
        assert (await judge.judge(call, provenance)).decision == "ALLOW"
        assert create.await_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_mid_live_shared_budget_stops_without_replaying_payment(tmp_path, provider):
    async def scenario():
        budget = DailyLLMBudget(2)
        args = {"amount":"29", "payee":"shop@chargers.example", "description":"charger"}
        order = SimpleNamespace(type="tool_use", name="create_order", input=args, id="order-1",
            model_dump=lambda **kwargs:{"type":"tool_use","name":"create_order","input":args,"id":"order-1"})
        agent_create = AsyncMock(return_value=SimpleNamespace(content=[order]))
        judge_create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text",text='{"decision":"ALLOW","reason":"authorized","source":"user:0","suspicious_excerpt":"","score_adjustment":0}')]))
        judge = build_judge("test-key",SimpleNamespace(messages=SimpleNamespace(create=judge_create)),budget=budget)
        audit = AuditStore(str(tmp_path/"audit.sqlite3"))
        policy = Policy.load("policies/default.yaml")
        # This test exercises shared agent/reviewer budget exhaustion. Put the
        # order in the gray zone; low-risk orders now correctly skip the judge.
        policy.auto_approve_threshold = Decimal("10")
        firewall = Firewall(policy, judge, audit)
        paypal = MockPayPal()
        paypal.execute = AsyncMock(wraps=paypal.execute)
        if provider == "openai":
            from openai.types.chat import ChatCompletion
            agent_create.return_value = ChatCompletion.model_validate({"id":"chat-test", "object":"chat.completion", "created":0, "model":"gpt-4o-mini",
                "choices":[{"index":0, "finish_reason":"tool_calls", "message":{"role":"assistant", "content":None,
                    "tool_calls":[{"id":"order-1", "type":"function", "function":{"name":"create_order", "arguments":json.dumps(args)}}]}}]})
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=agent_create)))
        else:
            client = SimpleNamespace(messages=SimpleNamespace(create=agent_create))
        agent = DemoAgent(Settings(agent_provider=provider, openai_api_key="test-key", anthropic_api_key="test-key"), firewall, paypal, ApprovalManager(),
            client=client,budget=budget)
        run = Run("Buy me a USB-C charger under $40",True,agent_mode="live")
        # The host has already opened this catalog product before checkout.
        run.selected_product = {"id":"budget-test-charger", "category":"electronics", "merchant":"marketplace.volt.example"}
        await agent.run(run)
        assert run.status == "completed"
        assert run.agent_mode == "live"  # Preserve the identity of the agent that actually ran.
        assert run.agent_provider == provider
        assert run.llm_degraded and run.judge_mode == "rules"
        assert any("stopped to avoid replaying" in item["content"] for item in run.messages)
        assert agent_create.await_count == judge_create.await_count == paypal.execute.await_count == 1
        assert budget.snapshot()["llm_calls_used"] == 2
        audit.close()
    asyncio.run(scenario())


def test_exhausted_api_degrades_agent_judge_and_recovers_next_day(tmp_path):
    app = create_app(Settings(agent_provider="anthropic", anthropic_api_key="test-key",agent_mode="live",daily_llm_call_limit=1,
        sqlite_path=str(tmp_path/"audit.sqlite3")),paypal=MockPayPal())
    today = [date(2026,11,9)]
    app.state.llm_budget.clock = lambda: today[0]
    app.state.llm_budget.reserve()
    app.state.agent.live = AsyncMock()
    with TestClient(app) as client:
        config = client.get("/api/config").json()
        assert config["llm_degraded"] and config["judge_mode"] == "rules"
        assert config["agent_mode"] == "compromised" and not config["live_available"]
        identifier = client.post("/api/runs",json={"message":"Buy me a USB-C charger under $40","agent_mode":"live"}).json()["id"]
        run = poll(client,identifier)
        assert run["agent_mode"] == "compromised" and run["judge_mode"] == "rules" and run["llm_degraded"]
        app.state.agent.live.assert_not_awaited()
        assert any(event.get("decision") == "ALLOW" for event in run["events"])
        today[0] = date(2026,11,10)
        config = client.get("/api/config").json()
        assert not config["llm_degraded"] and config["live_available"] and config["judge_mode"] == "anthropic"


def test_abuse_settings_env_and_validation(monkeypatch):
    monkeypatch.setenv("RUN_RATE_LIMIT_PER_MINUTE","7")
    monkeypatch.setenv("RUN_RATE_LIMIT_PER_HOUR","42")
    monkeypatch.setenv("DAILY_LLM_CALL_LIMIT","0")
    settings = Settings.from_env()
    assert (settings.run_rate_limit_per_minute,settings.run_rate_limit_per_hour,settings.daily_llm_call_limit) == (7,42,0)
    for kwargs in [{"run_rate_limit_per_minute":0},{"run_rate_limit_per_hour":-1},{"daily_llm_call_limit":-1}]:
        with pytest.raises(ValueError):
            Settings(**kwargs)
