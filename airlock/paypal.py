"""Sandbox payments powered by PayPal Agent Toolkit's framework-free tools.

Orders use the real 1.11.0 ``shared.orders.tool_handlers.create_order`` handler
and its schema/payload builder. A small client bridge retains our async HTTP
transport, bounded timeouts, request IDs and reviewed payee (not supported by
that toolkit schema), without the toolkit's requests-based logging client or an
agent framework. Payouts use REST because the toolkit has no payout tool.

Toolkit Invoicing creates bills or records payments already collected; it does
not send money to a contractor, so that scene remains a REST payout. Creating
an order does not capture payment; accepting a payout batch is not settlement.
"""
import asyncio
import json
import time
from decimal import Decimal

import httpx
from paypal_agent_toolkit.shared.orders import tool_handlers as order_tools


class MockPayPal:
    mode = "MOCK"
    mock = True

    async def execute(self, call, request_id):
        if call.tool not in {"create_order", "create_payout"}:
            raise ValueError("Unsupported payment tool")
        return {"id": f"MOCK-{'ORDER' if call.tool == 'create_order' else 'PAYOUT'}-{request_id[:12].upper()}",
                "status": "CREATED" if call.tool == "create_order" else "PENDING", "mock": True}

    async def close(self):
        pass


class _ToolkitOrderClient:
    """Implement the synchronous ``client.post`` interface used by the tool."""

    def __init__(self, paypal, call, request_id, loop):
        self.paypal, self.call = paypal, call
        self.request_id, self.loop = request_id, loop

    def post(self, uri, payload):
        if uri != "/v2/checkout/orders" or payload.get("intent") != "CAPTURE":
            raise ValueError("Unexpected toolkit order operation")
        units = payload.get("purchase_units", [])
        if len(units) != 1:
            raise ValueError("Unexpected toolkit purchase units")
        unit = units[0]
        amount = unit["amount"]
        # Toolkit 1.11.0 uses floats internally. Check its serialized money
        # against the reviewed Decimal before any OAuth or payment request.
        if (amount["currency_code"] != self.call.currency
                or Decimal(amount["value"]) != self.call.amount):
            raise ValueError("Toolkit order amount differs from reviewed payment")
        unit["payee"] = {"email_address": self.call.payee}
        unit["reference_id"] = self.request_id
        unit["description"] = self.call.description
        return asyncio.run_coroutine_threadsafe(
            self.paypal._post(uri, payload, self.request_id), self.loop
        ).result()


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
        response = await self.client.post("/v1/oauth2/token", auth=(self.client_id, self._secret), data={"grant_type": "client_credentials"}, headers={"Accept": "application/json"})
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._expires = time.monotonic() + max(0, int(body.get("expires_in", 300)) - 30)
        return self._token

    async def _post(self, uri, payload, request_id):
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "PayPal-Request-Id": request_id}
        response = await self.client.post(uri, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    async def execute(self, call, request_id):
        if call.tool not in {"create_order", "create_payout"}:
            raise ValueError("Unsupported payment tool")
        if call.currency != "USD":
            raise ValueError("Only USD payments are supported")
        if call.tool == "create_order":
            params = {
                "currency_code": call.currency,
                "items": [{"name": call.description[:127] or "Airlock order", "description": call.description,
                           "quantity": 1, "item_cost": str(call.amount), "item_total": str(call.amount)}],
                "return_url": None, "cancel_url": None,
            }
            bridge = _ToolkitOrderClient(self, call, request_id, asyncio.get_running_loop())
            # The official handler is synchronous. HTTP runs on this event loop
            # through the bridge so approvals and polling remain responsive.
            raw = await asyncio.to_thread(order_tools.create_order, bridge, params)
            result = json.loads(raw)
            return {"id": result["id"], "status": result["status"], "mock": False}
        result = await self._post("/v1/payments/payouts", {
            "sender_batch_header": {"sender_batch_id": request_id, "email_subject": "Airlock sandbox payout"},
            "items": [{"recipient_type": "EMAIL", "receiver": call.payee,
                       "amount": {"value": f"{call.amount:.2f}", "currency": call.currency},
                       "note": call.description, "sender_item_id": request_id}],
        }, request_id)
        batch = result["batch_header"]
        return {"id": batch["payout_batch_id"], "status": batch["batch_status"], "mock": False}

    async def close(self):
        await self.client.aclose()


def build_paypal(client_id=None, secret=None):
    if bool(client_id) != bool(secret):
        raise ValueError("Set both PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET, or leave both empty for MOCK")
    return SandboxPayPal(client_id, secret) if client_id and secret else MockPayPal()
