"""Sandbox-only REST adapter; official toolkit install unavailable in build sandbox.

Sources: https://developer.paypal.com/docs/api/orders/v2/
https://developer.paypal.com/docs/api/payments.payouts-batch/v1/
Create order does not capture payment. Payout batch acceptance is not settlement.
"""
import time
import httpx


class MockPayPal:
    mode = "MOCK"
    mock = True

    async def execute(self, call, request_id):
        if call.tool not in {"create_order", "create_payout"}:
            raise ValueError("Unsupported payment tool")
        return {"id":f"MOCK-{'ORDER' if call.tool == 'create_order' else 'PAYOUT'}-{request_id[:12].upper()}",
                "status":"CREATED" if call.tool == "create_order" else "PENDING", "mock":True}

    async def close(self):
        pass


class SandboxPayPal:
    mode = "SANDBOX"
    mock = False
    BASE_URL = "https://api-m.sandbox.paypal.com"

    def __init__(self, client_id, secret, *, transport=None):
        self.client_id, self._secret = client_id, secret
        self.client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=25, transport=transport)
        self._token = None
        self._expires = 0

    async def _access_token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        response = await self.client.post("/v1/oauth2/token", auth=(self.client_id,self._secret), data={"grant_type":"client_credentials"}, headers={"Accept":"application/json"})
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._expires = time.monotonic()+max(0,int(body.get("expires_in",300))-30)
        return self._token

    async def execute(self, call, request_id):
        token = await self._access_token()
        headers = {"Authorization":f"Bearer {token}", "Content-Type":"application/json", "PayPal-Request-Id":request_id}
        amount = {"currency_code":call.currency, "value":f"{call.amount:.2f}"}
        if call.tool == "create_order":
            response = await self.client.post("/v2/checkout/orders", headers=headers, json={"intent":"CAPTURE", "purchase_units":[{"reference_id":request_id,"description":call.description,"amount":amount,"payee":{"email_address":call.payee}}]})
            response.raise_for_status()
            result = response.json()
            return {"id":result["id"],"status":result["status"],"mock":False}
        if call.tool == "create_payout":
            response = await self.client.post("/v1/payments/payouts", headers=headers, json={"sender_batch_header":{"sender_batch_id":request_id,"email_subject":"Airlock sandbox payout"}, "items":[{"recipient_type":"EMAIL", "receiver":call.payee,"amount":{"value":amount["value"],"currency":call.currency},"note":call.description,"sender_item_id":request_id}]})
            response.raise_for_status()
            result = response.json()["batch_header"]
            return {"id":result["payout_batch_id"],"status":result["batch_status"],"mock":False}
        raise ValueError("Unsupported payment tool")

    async def close(self):
        await self.client.aclose()


def build_paypal(client_id=None, secret=None):
    if bool(client_id) != bool(secret):
        raise ValueError("Set both PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET, or leave both empty for MOCK")
    return SandboxPayPal(client_id,secret) if client_id and secret else MockPayPal()
