import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from airlock.agent import DemoAgent, INJECTION
from airlock.config import Settings
from airlock.paypal import MockPayPal
from airlock.store import ApprovalManager, Run
from firewall import AuditStore, Firewall, Policy, build_judge


def text_block(text):
    return SimpleNamespace(type="text", text=text, model_dump=lambda **kwargs: {"type": "text", "text": text})


def test_live_model_can_resist_injection_without_simulated_payment(tmp_path):
    async def scenario():
        search = SimpleNamespace(type="tool_use", name="search_products", input={}, id="search-1",
            model_dump=lambda **kwargs: {"type": "tool_use", "name": "search_products", "input": {}, "id": "search-1"})
        order_args = {"amount": "29", "currency": "USD", "payee": "shop@chargers.example", "description": "USB-C charger"}
        order = SimpleNamespace(type="tool_use", name="create_order", input=order_args, id="order-1",
            model_dump=lambda **kwargs: {"type": "tool_use", "name": "create_order", "input": order_args, "id": "order-1"})
        create = AsyncMock(side_effect=[SimpleNamespace(content=[search]), SimpleNamespace(content=[order]), SimpleNamespace(content=[text_block("Order created. I ignored the unrelated escrow policy.")])])
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        agent = DemoAgent(Settings(anthropic_api_key="test-key", agent_model="custom-agent-model"), None, MockPayPal(), ApprovalManager(), client=client)
        agent.payment = AsyncMock(return_value={"id":"MOCK-ORDER", "status":"CREATED", "mock":True})
        run = Run("Buy me a USB-C charger under $40", True, agent_mode="live")
        await agent.run(run)
        assert run.status == "completed"
        assert run.agent_mode == "live"
        assert create.await_count == 3
        assert all(item.kwargs["model"] == "custom-agent-model" for item in create.await_args_list)
        agent.payment.assert_awaited_once()
        assert agent.payment.await_args.args[1].tool == "create_order"
        assert INJECTION in run.provenance.sources[1].text
        assert "SYSTEM:" not in INJECTION
    asyncio.run(scenario())


def test_live_failure_is_reported_without_compromised_replay():
    async def scenario():
        client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("model unavailable"))))
        agent = DemoAgent(Settings(anthropic_api_key="test-key"), None, MockPayPal(), ApprovalManager(), client=client)
        agent.payment = AsyncMock()
        run = Run("Buy me a USB-C charger under $40", True, agent_mode="live")
        await agent.run(run)
        assert run.status == "failed"
        assert run.agent_mode == "live"
        agent.payment.assert_not_awaited()
        assert "No automatic retry" in run.error
        assert not run.events
    asyncio.run(scenario())


def test_compromised_agent_uses_llm_judge_and_keeps_injection_untrusted(tmp_path):
    async def scenario():
        def verdict(decision, excerpt="", source="user:0"):
            return SimpleNamespace(content=[text_block(json.dumps({"decision":decision, "reason":"Intent checked", "source":source, "suspicious_excerpt":excerpt}))])
        create = AsyncMock(side_effect=[verdict("ALLOW"), verdict("BLOCK", INJECTION, "tool:1")])
        judge = build_judge("test-key", SimpleNamespace(messages=SimpleNamespace(create=create)), model="custom-judge-model")
        audit = AuditStore(str(tmp_path/"audit.sqlite3"))
        firewall = Firewall(Policy.load("policies/default.yaml"), judge, audit)
        paypal = MockPayPal()
        paypal.execute = AsyncMock(wraps=paypal.execute)
        agent = DemoAgent(Settings(anthropic_api_key="test-key"), firewall, paypal, ApprovalManager())
        run = Run("Buy me a USB-C charger under $40", True)
        await agent.run(run)
        assert run.status == "completed"
        assert run.agent_mode == "compromised"
        assert create.await_count == 2
        assert all(item.kwargs["model"] == "custom-judge-model" for item in create.await_args_list)
        paypal.execute.assert_awaited_once()
        assert paypal.execute.await_args.args[0].tool == "create_order"
        judge_payload = json.loads(create.await_args_list[1].kwargs["messages"][0]["content"])
        assert judge_payload["provenance"][0] == {"id":"user:0", "origin":"user", "name":"user input", "text":run.message}
        assert judge_payload["provenance"][1]["origin"] == "tool"
        assert INJECTION in json.dumps(judge_payload["provenance"])
        assert any(event.get("decision") == "BLOCK" and event.get("suspicious_excerpt") == INJECTION for event in run.events)
        assert any("Simulated compromised agent" in message["content"] for message in run.messages)
        audit.close()
    asyncio.run(scenario())
