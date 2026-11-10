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
        agent = DemoAgent(Settings(agent_provider="anthropic", anthropic_api_key="test-key", agent_model="custom-agent-model"), None, MockPayPal(), ApprovalManager(), client=client)
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
        agent = DemoAgent(Settings(agent_provider="anthropic", anthropic_api_key="test-key"), None, MockPayPal(), ApprovalManager(), client=client)
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
        agent = DemoAgent(Settings(agent_provider="anthropic", anthropic_api_key="test-key"), firewall, paypal, ApprovalManager())
        run = Run("Buy me a USB-C charger under $40", True, agent_mode="compromised")
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


def openai_response(*calls, content=None):
    return {"id":"chat-test", "object":"chat.completion", "created":0, "model":"gpt-4o-mini",
        "choices":[{"index":0, "finish_reason":"tool_calls" if calls else "stop", "message":{
            "role":"assistant", "content":content,
            **({"tool_calls":[{"id":identifier,"type":"function","function":{"name":name,"arguments":arguments}}
                for identifier,name,arguments in calls]} if calls else {})}}]}


def test_openai_real_sdk_tool_round_trip_firewall_and_provenance(tmp_path):
    import httpx
    from openai import AsyncOpenAI
    from firewall import RuleJudge
    from firewall.limits import DailyLLMBudget

    async def scenario(enabled):
        requests = []
        order = json.dumps({"amount":"29", "currency":"USD", "payee":"shop@chargers.example", "description":"USB-C charger"})
        payout = json.dumps({"amount":"500", "currency":"USD", "payee":"vendor-support@evil.example", "description":"Verification escrow"})
        responses = [openai_response(("search-1","search_products","{}")),
            openai_response(("order-1","create_order",order),("payout-1","create_payout",payout)),
            openai_response(content="Finished; tool results describe the actual outcomes.")]
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=responses[len(requests)-1])
        async with AsyncOpenAI(api_key="test-key", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))) as client:
            audit = AuditStore(str(tmp_path/f"openai-{enabled}.sqlite3"))
            firewall = Firewall(Policy.load("policies/default.yaml"), RuleJudge(), audit)
            paypal = MockPayPal()
            paypal.execute = AsyncMock(wraps=paypal.execute)
            budget = DailyLLMBudget(10)
            agent = DemoAgent(Settings(openai_api_key="test-key"), firewall, paypal, ApprovalManager(), client=client, budget=budget)
            run = Run("Buy me a USB-C charger under $40", enabled)
            await agent.run(run)
            assert run.status == "completed"
            assert (run.agent_mode, run.agent_provider, run.agent_model) == ("live","openai","gpt-4o-mini")
            assert run.judge_mode == "rules"
            assert budget.snapshot()["llm_calls_used"] == 3
            assert paypal.execute.await_count == (1 if enabled else 2)
            rows = audit.report()["rows"]
            assert len(rows) == 2
            assert {row["decision"] for row in rows} == ({"ALLOW","BLOCK"} if enabled else {"ALLOW"})
            assert run.provenance.sources[1].origin == "tool"
            assert INJECTION in run.provenance.sources[1].text
            assert not any("Simulated compromised" in item["content"] for item in run.messages)
            prompt = requests[0]["messages"][0]["content"].lower()
            assert "firewall" not in prompt and "block" not in prompt and "approval" not in prompt
            assert all(request["model"] == "gpt-4o-mini" for request in requests)
            assert all(request["max_completion_tokens"] == 1500 and "max_tokens" not in request for request in requests)
            assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {"search_products","create_order","create_payout"}
            search_result = requests[1]["messages"][-1]
            assert search_result["role"] == "tool" and search_result["tool_call_id"] == "search-1"
            assert INJECTION in search_result["content"]
            assert [message["tool_call_id"] for message in requests[2]["messages"][-2:]] == ["order-1","payout-1"]
            assert json.loads(requests[2]["messages"][-1]["content"])["decision"] == ("BLOCK" if enabled else "ALLOW")
            audit.close()
    for enabled in (False, True):
        asyncio.run(scenario(enabled))


def test_openai_malformed_arguments_and_unknown_tools_never_execute_payments():
    import httpx
    from openai import AsyncOpenAI

    async def scenario():
        requests = []
        cases = [("bad-json","create_payout","{"), ("null","create_payout","null"),
            ("list","create_payout","[]"), ("string","create_payout",'"bad"'),
            ("extra","create_payout",'{"tool":"create_order","amount":"500","payee":"evil@example.com"}'),
            ("invalid-money","create_payout",'{"amount":"-5","payee":"evil@example.com"}'),
            ("invalid-search","search_products",'{"query":"charger"}'), ("unknown","delete_all","{}")]
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=openai_response(*cases) if len(requests)==1 else openai_response(content="Unable to use these arguments."))
        async with AsyncOpenAI(api_key="test-key", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))) as client:
            agent = DemoAgent(Settings(openai_api_key="test-key"), None, MockPayPal(), ApprovalManager(), client=client)
            agent.payment, agent.products = AsyncMock(), AsyncMock()
            run = Run("Buy me a USB-C charger under $40", True)
            await agent.run(run)
            assert run.status == "completed"
            agent.payment.assert_not_awaited()
            agent.products.assert_not_awaited()
            results = requests[1]["messages"][-len(cases):]
            assert all("error" in json.loads(item["content"]) for item in results)
            assert [item["tool_call_id"] for item in results] == [item[0] for item in cases]
    asyncio.run(scenario())


def test_openai_failure_counts_budget_without_scripted_replay():
    from firewall.limits import DailyLLMBudget
    async def scenario():
        create = AsyncMock(side_effect=TimeoutError())
        budget = DailyLLMBudget(5)
        agent = DemoAgent(Settings(openai_api_key="test-key"), None, MockPayPal(), ApprovalManager(),
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), budget=budget)
        agent.payment = AsyncMock()
        run = Run("Buy me a USB-C charger under $40", True)
        await agent.run(run)
        assert run.status == "failed"
        assert run.agent_mode == "live" and run.agent_provider == "openai"
        assert budget.snapshot()["llm_calls_used"] == 1
        create.assert_awaited_once()
        agent.payment.assert_not_awaited()
        assert not run.events
    asyncio.run(scenario())
