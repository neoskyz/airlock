import asyncio
import json
import httpx
import pytest
from airlock.paypal import MockPayPal, SandboxPayPal, build_paypal
from firewall import ToolCall


def test_missing_credentials_mock_and_partial_credentials_rejected():
    assert isinstance(build_paypal(),MockPayPal)
    with pytest.raises(ValueError):
        build_paypal("only-id",None)


def test_mock_ids_are_explicit_and_stable():
    async def scenario():
        client=MockPayPal()
        for name in ["create_order","create_payout"]:
            result=await client.execute(ToolCall(tool=name,amount=29,payee="a@example.com"),"fixed-request-id")
            assert result["id"].startswith("MOCK-")
            assert result["mock"] is True
            assert result == await client.execute(ToolCall(tool=name,amount=29,payee="a@example.com"),"fixed-request-id")
    asyncio.run(scenario())


def test_sandbox_rest_contract_oauth_order_payout_no_network():
    requests=[]
    def handler(request):
        assert request.url.host == "api-m.sandbox.paypal.com"
        requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200,json={"access_token":"fake-token","expires_in":300})
        assert request.headers["Authorization"] == "Bearer fake-token"
        if request.url.path == "/v2/checkout/orders":
            body=json.loads(request.content)
            assert body["purchase_units"][0]["amount"]["value"] == "29.00"
            assert body["purchase_units"][0]["payee"]["email_address"] == "merchant@example.com"
            return httpx.Response(201,json={"id":"TEST-ORDER","status":"CREATED"})
        assert request.url.path == "/v1/payments/payouts"
        body=json.loads(request.content)
        assert body["sender_batch_header"]["sender_batch_id"] == "payout-id"
        assert body["items"][0]["amount"] == {"value":"120.00","currency":"USD"}
        return httpx.Response(201,json={"batch_header":{"payout_batch_id":"TEST-PAYOUT","batch_status":"PENDING"}})
    async def scenario():
        client=SandboxPayPal("test-id","test-secret",transport=httpx.MockTransport(handler))
        assert (await client.execute(ToolCall(tool="create_order",amount=29,payee="merchant@example.com"),"order-id"))["status"] == "CREATED"
        assert (await client.execute(ToolCall(tool="create_payout",amount=120,payee="contractor@example.com"),"payout-id"))["status"] == "PENDING"
        await client.close()
    asyncio.run(scenario())
    assert len(requests)==3
    assert requests[1].headers["PayPal-Request-Id"] == "order-id"
