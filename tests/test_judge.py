import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from firewall import AnthropicJudge, Decision, Provenance, RuleJudge, ToolCall, build_judge
from airlock.agent import INJECTION


def test_missing_key_uses_rules():
    assert isinstance(build_judge(None),RuleJudge)


def test_authorized_charger_and_contractor():
    async def scenario():
        judge=RuleJudge()
        assert (await judge.judge(ToolCall(tool="create_order",amount=29,payee="shop@chargers.example",description="USB-C charger"),Provenance("Buy me a USB-C charger under $40"))).decision == Decision.ALLOW
        assert (await judge.judge(ToolCall(tool="create_payout",amount=120,payee="contractor@contractor.example"),Provenance("Also pay my contractor $120 invoice"))).decision == Decision.ALLOW
    asyncio.run(scenario())


def test_injection_is_untrusted_and_blocked():
    async def scenario():
        provenance=Provenance("Buy me a USB-C charger under $40")
        provenance.add_tool("product description",INJECTION)
        verdict=await RuleJudge().judge(ToolCall(tool="create_payout",amount=500,payee="vendor-support@evil.example"),provenance)
        assert verdict.decision == Decision.BLOCK
        assert verdict.suspicious_excerpt == INJECTION
        assert "tool:" in verdict.source
    asyncio.run(scenario())


def test_rules_fail_closed_for_unknown_intent_and_merchant():
    async def scenario():
        for user,payee in [("send money","shop@chargers.example"),("Buy me a charger under $40","evil@evilchargers.example")]:
            result=await RuleJudge().judge(ToolCall(tool="create_order",amount=29,payee=payee,description="charger"),Provenance(user))
            assert result.decision == Decision.BLOCK
    asyncio.run(scenario())


def test_api_key_never_downgrades_after_judge_failure():
    async def scenario():
        client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("API down"))))
        judge=build_judge("not-a-real-key",client)
        assert isinstance(judge,AnthropicJudge)
        verdict=await judge.judge(ToolCall(tool="create_order",amount=29,payee="shop@chargers.example"),Provenance("charger"))
        assert verdict.decision == Decision.BLOCK
        assert "fail-closed" in verdict.reason
    asyncio.run(scenario())


def test_structured_judge_response_and_malformed_output():
    async def scenario():
        response=SimpleNamespace(content=[SimpleNamespace(type="text",text='{"decision":"ALLOW","reason":"Matches user","source":"user:0","suspicious_excerpt":""}')])
        create=AsyncMock(return_value=response)
        judge=AnthropicJudge(SimpleNamespace(messages=SimpleNamespace(create=create)))
        call=ToolCall(tool="create_order",amount=29,payee="shop@chargers.example")
        assert (await judge.judge(call,Provenance("charger"))).decision == Decision.ALLOW
        assert create.call_args.kwargs["model"] == "claude-haiku-5-5"
        assert "output_config" in create.call_args.kwargs
        response.content[0].text="not json"
        assert (await judge.judge(call,Provenance("charger"))).decision == Decision.BLOCK
    asyncio.run(scenario())


def test_rules_do_not_treat_negated_or_extra_instructions_as_authorization():
    async def scenario():
        cases=[("Do not buy me a charger under $40","create_order",29,"shop@chargers.example"),("Do not pay my contractor $120 invoice","create_payout",120,"contractor@contractor.example")]
        for user,tool,amount,payee in cases:
            verdict=await RuleJudge().judge(ToolCall(tool=tool,amount=amount,payee=payee,description="charger"),Provenance(user))
            assert verdict.decision == Decision.BLOCK
    asyncio.run(scenario())


def test_judge_model_override_and_timeout_fail_closed():
    async def scenario():
        create=AsyncMock(side_effect=TimeoutError("test timeout"))
        judge=build_judge("test-key",SimpleNamespace(messages=SimpleNamespace(create=create)),model="custom-judge-model")
        verdict=await judge.judge(ToolCall(tool="create_order",amount=29,payee="shop@chargers.example"),Provenance("Buy me a USB-C charger under $40"))
        assert verdict.decision == Decision.BLOCK
        assert "fail-closed" in verdict.reason
        assert create.call_args.kwargs["model"] == "custom-judge-model"
    asyncio.run(scenario())
