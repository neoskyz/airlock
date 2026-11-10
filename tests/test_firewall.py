import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock
from firewall import AuditStore, Decision, Firewall, Policy, Provenance, RuleJudge, ToolCall
from airlock.agent import INJECTION


def setup(daily=2000):
    store=AuditStore(":memory:")
    policy=Policy(single_transaction_limit=1000,daily_limit=daily,auto_approve_threshold=100,allowed_payees=["shop@chargers.example","shop@chargers.example"])
    return Firewall(policy,RuleJudge(),store),store


def test_attack_block_and_bypass_are_audited():
    async def scenario():
        firewall,store=setup()
        p=Provenance("Buy me a USB-C charger under $40");p.add_tool("description",INJECTION)
        call=ToolCall(tool="create_payout",amount=500,payee="vendor-support@evil.example")
        execute=AsyncMock(return_value={"id":"MOCK-PAYOUT","status":"PENDING"})
        assert (await firewall.invoke(call,p,execute))["decision"] == "BLOCK"
        execute.assert_not_called()
        assert (await firewall.invoke(call,p,execute,enabled=False))["id"] == "MOCK-PAYOUT"
        execute.assert_awaited_once()
        report=store.report()
        assert report["stats"] == {"blocked_amount":500,"allowed_amount":500}
        assert len(report["rows"]) == 2
        store.close()
    asyncio.run(scenario())


def test_approval_really_suspends_and_denial_does_not_execute():
    async def scenario():
        firewall,store=setup()
        call=ToolCall(tool="create_order",amount="249.99",payee="shop@chargers.example", merchant="sony.com", category="electronics", description="Noise-cancelling headphones")
        future=asyncio.get_running_loop().create_future()
        execute=AsyncMock(return_value={"id":"MOCK-PAYOUT","status":"PENDING"})
        task=asyncio.create_task(firewall.invoke(call,Provenance("Buy me noise-cancelling headphones under $300"),execute,request_approval=lambda *args:future))
        await asyncio.sleep(0)
        execute.assert_not_called()
        assert store.report()["rows"][0]["status"] == "awaiting_approval"
        future.set_result(False)
        assert (await task)["status"] == "denied"
        execute.assert_not_called()
        assert store.reserve_total() == 0
        # A separate authorized approval executes exactly once.
        approval=AsyncMock(return_value=True)
        assert (await firewall.invoke(call,Provenance("Buy me noise-cancelling headphones under $300"),execute,request_approval=approval))["id"] == "MOCK-PAYOUT"
        execute.assert_awaited_once()
        assert store.report()["stats"]["allowed_amount"] == 249.99
        store.close()
    asyncio.run(scenario())


def test_pending_approvals_reserve_daily_budget_across_midnight():
    async def scenario():
        firewall,store=setup(daily=400)
        call=ToolCall(tool="create_order",amount="249.99",payee="shop@chargers.example", merchant="sony.com", category="electronics", description="Noise-cancelling headphones")
        future=asyncio.get_running_loop().create_future()
        execute=AsyncMock(return_value={"id":"MOCK-PAYOUT","status":"PENDING"})
        p=Provenance("Buy me noise-cancelling headphones under $300")
        task=asyncio.create_task(firewall.invoke(call,p,execute,request_approval=lambda *args:future))
        await asyncio.sleep(0)
        store.connection.execute("UPDATE audit SET timestamp='2000-01-01T00:00:00Z',reserved_day='2000-01-01'")
        store.connection.commit()
        assert store.reserve_total() == Decimal("249.99")
        blocked=await firewall.invoke(call,p,execute)
        assert blocked["decision"] == "BLOCK"
        future.set_result(True)
        await task
        assert store.reserve_total() == Decimal("249.99")
        execute.assert_awaited_once()
        store.close()
    asyncio.run(scenario())


def test_provider_failure_not_counted_as_success_and_remains_reserved():
    async def scenario():
        firewall,store=setup()
        result=await firewall.invoke(ToolCall(tool="create_order",amount=29,payee="shop@chargers.example",description="charger"),Provenance("Buy me a charger under $40"),AsyncMock(side_effect=TimeoutError()))
        assert result["status"] == "unknown"
        assert store.report()["stats"]["allowed_amount"] == 0
        assert store.reserve_total() == 29
        store.close()
    asyncio.run(scenario())


def test_judge_exception_blocks_before_executor():
    async def scenario():
        firewall,store=setup();firewall.judge=type("Broken",(),{"judge":AsyncMock(side_effect=ValueError())})()
        executor=AsyncMock()
        result=await firewall.invoke(ToolCall(tool="create_order",amount=29,payee="shop@chargers.example"),Provenance("charger"),executor)
        assert result["decision"] == "BLOCK"
        executor.assert_not_called();store.close()
    asyncio.run(scenario())


def test_malformed_judge_result_is_fail_closed():
    async def scenario():
        firewall,store=setup();firewall.judge=type("Broken",(),{"judge":AsyncMock(return_value=None)})()
        executor=AsyncMock()
        result=await firewall.invoke(ToolCall(tool="create_order",amount=29,payee="shop@chargers.example"),Provenance("charger"),executor)
        assert result["decision"] == "BLOCK"
        executor.assert_not_called();store.close()
    asyncio.run(scenario())
