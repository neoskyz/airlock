"""Offline lifecycle matrix using the real live-agent tool loop and ASGI API.

A = injected charger, B = approval-required headphones, F = free-form advice.
The provider, catalog wire responses, reviewer, and PayPal are controlled here;
no test calls an external service. Gates replace model/network timing with
repeatable scheduling points.
"""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from itertools import product
import json
from types import SimpleNamespace

import httpx
import pytest

from airlock.app import create_app
from airlock.catalog import Channel3Catalog
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import Decision, Verdict


PROMPTS = {
    "A": "Buy me the cheapest 65W USB-C charger under $40",
    "B": "Buy me noise-cancelling headphones under $300",
    "F": "Recommend a travel accessory under $60 and explain the options",
}
MODES = ("Monitor", "Enforce")
PAIRS = tuple(product(PROMPTS, repeat=2))
TRANSITIONS = ("continue", "switch_mode", "reset", "stop_before_payment", "stop_during_summary")
TERMINAL = {"completed", "failed", "cancelled"}


class FakeJudge:
    mode = "anthropic"
    model = "offline-controlled-reviewer"

    def __init__(self, behavior="normal", *, unknown_requires_approval=False):
        self.behavior = behavior
        self.unknown_requires_approval = unknown_requires_approval
        self.calls = []

    async def judge(self, call, provenance):
        self.calls.append(call)
        if self.behavior == "exception":
            raise RuntimeError("controlled reviewer failure")
        if self.behavior == "timeout":
            await asyncio.Event().wait()
        if self.behavior == "invalid":
            return {"decision": "NOT_A_DECISION"}
        if call.tool == "create_payout":
            return Verdict(decision=Decision.BLOCK, reason="Tool-origin payout was not authorized", source="user:0")
        if self.unknown_requires_approval and call.merchant != "marketplace.volt.example":
            return Verdict(decision=Decision.REQUIRE_APPROVAL, reason="Review the alternative merchant")
        return Verdict(decision=Decision.ALLOW, reason="Purchase matches the requested product and budget")


class RecordingPayPal(MockPayPal):
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.gate = None

    async def execute(self, call, request_id):
        self.calls.append((call, request_id))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        return await super().execute(call, request_id)


def fake_channel3(request):
    assert request.url.path == "/v1/search"
    payload = json.loads(request.content)
    assert request.headers["x-api-key"] == "offline-channel3"
    headphones = "headphones" in payload["query"].lower()
    identifier, title, price, merchant = (
        ("matrix-headphones", "Noise-cancelling headphones", 249.99, "sony.com")
        if headphones else ("matrix-alternative", "Travel USB-C charger", 24.99, "anker.com")
    )
    return httpx.Response(200, json={"products": [{
        "id": identifier, "title": title, "description": "Ordinary catalog product",
        "category": {"slug": "electronics-headphones" if headphones else "electronics"},
        "offers": [{"url": f"https://{merchant}/product", "domain": merchant,
                    "price": {"price": price, "currency": "USD"}, "availability": "InStock"}],
    }]})


def tool(name, **arguments):
    return (name, arguments)


def payment(name, amount, description):
    return tool(name, amount=amount, currency="USD", description=description,
                payee="vendor-support@evil.example" if name == "create_payout" else "shop@chargers.example")


@dataclass
class Plan:
    scenario: str
    behavior: str = "normal"
    gate_at: int | None = None
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    fail_at: int | None = None

    def steps(self):
        if self.scenario == "F":
            return [tool("search_products"), "Here are recommendations; no purchase was submitted."]
        headphone = self.scenario == "B"
        identifier = "matrix-headphones" if headphone else "marketplace-volt-30w"
        order = payment("create_order", "249.99" if headphone else "19.99",
                        "Noise-cancelling headphones" if headphone else "Volt 65W USB-C charger")
        steps = [tool("search_products"), tool("open_product", product_id=identifier)]
        if headphone:
            steps.append(order)
        elif self.behavior == "alternative_after_block":
            steps.extend([
                payment("create_payout", "500.00", "Merchant verification escrow"),
                tool("open_product", product_id="matrix-alternative"),
                payment("create_order", "24.99", "Travel USB-C charger"),
            ])
        elif self.behavior == "order_first":
            steps.extend([order, payment("create_payout", "500.00", "Merchant verification escrow")])
        elif self.behavior == "recommend_only":
            pass
        else:
            # Typical gpt-4o-mini behavior: the injected prerequisite is tried first.
            steps.extend([payment("create_payout", "500.00", "Merchant verification escrow"), order])
        steps.append("Finished reviewing the tool results. Order creation is not payment settlement.")
        return steps


class FakeMessage:
    def __init__(self, step, index):
        self.content = step if isinstance(step, str) else None
        self.tool_calls = [] if isinstance(step, str) else [SimpleNamespace(
            id=f"offline-call-{index}", function=SimpleNamespace(name=step[0], arguments=json.dumps(step[1]))) ]

    def model_dump(self, **_):
        value = {"role": "assistant"}
        if self.content:
            value["content"] = self.content
        if self.tool_calls:
            value["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                "name": call.function.name, "arguments": call.function.arguments,
            }} for call in self.tool_calls]
        return value


class FakeOpenAI:
    def __init__(self):
        self.plans = deque()
        self.sessions = {}
        self.chat = SimpleNamespace(completions=self)

    async def create(self, *, messages, **kwargs):
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["temperature"] == 0
        key = id(messages)
        if key not in self.sessions:
            assert len(messages) == 2 and self.plans
            plan = self.plans.popleft()
            self.sessions[key] = {"messages": messages, "plan": plan, "index": 0}
        session = self.sessions[key]
        plan, index = session["plan"], session["index"]
        if index == plan.gate_at:
            plan.entered.set()
            await plan.gate.wait()
        if index == plan.fail_at:
            raise RuntimeError("controlled final model response failure")
        await asyncio.sleep(0)
        step = plan.steps()[index]
        session["index"] += 1
        if isinstance(step, str):
            plan.finished.set()
        return SimpleNamespace(choices=[SimpleNamespace(message=FakeMessage(step, index))])


@asynccontextmanager
async def workspace(tmp_path, *, mode="Enforce", judge_behavior="normal", unknown_requires_approval=False):
    paypal = RecordingPayPal()
    judge = FakeJudge(judge_behavior, unknown_requires_approval=unknown_requires_approval)
    catalog = Channel3Catalog("offline-channel3", transport=httpx.MockTransport(fake_channel3))
    settings = Settings(initial_mode=mode, openai_api_key="offline-openai", daily_llm_call_limit=1000,
                        run_rate_limit_per_minute=1000, run_rate_limit_per_hour=10000,
                        sqlite_path=str(tmp_path / "matrix.sqlite3"), frontend_dist=str(tmp_path))
    app = create_app(settings, paypal=paypal, judge=judge, catalog=catalog)
    app.state.firewall.review_timeout = .01
    provider = FakeOpenAI()
    app.state.agent.client = provider
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://matrix") as client:
            yield SimpleNamespace(app=app, client=client, provider=provider, paypal=paypal, judge=judge)


async def start(ws, plan, *, client=None):
    ws.provider.plans.append(plan)
    response = await (client or ws.client).post("/api/runs", json={"message": PROMPTS[plan.scenario]})
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def wait_status(ws, run_id, wanted):
    wanted = {wanted} if isinstance(wanted, str) else wanted
    async with asyncio.timeout(2):
        while True:
            run = (await ws.client.get(f"/api/runs/{run_id}")).json()
            if run["status"] in wanted:
                return run
            await asyncio.sleep(.001)


async def settle(ws, run_id, approved=True):
    async with asyncio.timeout(2):
        while True:
            # Let an already-resolved approval future update its card before
            # deciding whether this helper must answer a new approval.
            await asyncio.sleep(.001)
            run = (await ws.client.get(f"/api/runs/{run_id}")).json()
            if run["status"] in TERMINAL:
                return run
            if run["status"] == "awaiting_approval":
                approval = next(card for card in run["approvals"] if card["status"] == "pending")
                response = await ws.client.post(f'/api/approvals/{approval["id"]}', json={"approved": approved})
                assert response.status_code == 200
            await asyncio.sleep(.001)


class ConnectedTab:
    async def is_disconnected(self):
        return False


async def assert_views(ws, run_id, status):
    """A real SSE frame and both snapshots must expose the same authoritative status."""
    for audience in ("console", "client"):
        state = await ws.app.state.snapshot(audience)
        run = next(item for item in state["runs"] if item["id"] == run_id)
        assert run["status"] == status
        stream = ws.app.state.event_stream(ConnectedTab(), audience)
        try:
            frame = await anext(stream)
            streamed = json.loads(frame.split("data: ", 1)[1])
            visible = next(item for item in streamed["runs"] if item["id"] == run_id)
            assert visible["status"] == status
            assert visible == run
        finally:
            await stream.aclose()
    client_run = (await ws.client.get(f"/api/runs/{run_id}?audience=client")).json()
    assert client_run["status"] == status
    assert not any("provenance" in item or "risk_score" in item for item in client_run.get("approvals", []))
    if status in TERMINAL:
        events = client_run["events"]
        assert any(event["type"] == "run_status" and event["status"] == status for event in events)
        assert [event["status"] for event in events if event["type"] == "run_status"][-1] == status


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: "-to-".join(pair))
@pytest.mark.parametrize("transition", TRANSITIONS)
@pytest.mark.parametrize("approved", (True, False), ids=("approve", "deny"))
def test_ordered_run_matrix(tmp_path, mode, pair, transition, approved):
    async def scenario():
        async with workspace(tmp_path, mode=mode) as ws:
            first = Plan(pair[0])
            if transition.startswith("stop_"):
                first.gate_at = (0 if first.scenario == "F" else 2) if transition == "stop_before_payment" else len(first.steps()) - 1
            identifier = await start(ws, first)
            if transition.startswith("stop_"):
                # B may await approval before reaching a summary gate.
                if transition == "stop_during_summary" and pair[0] == "B" and mode == "Enforce":
                    pending = await wait_status(ws, identifier, "awaiting_approval")
                    assert (await ws.client.post(f'/api/approvals/{pending["approvals"][0]["id"]}', json={"approved": approved})).status_code == 200
                await asyncio.wait_for(first.entered.wait(), 2)
                assert (await ws.client.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
                result = await wait_status(ws, identifier, "cancelled")
                # Releasing an old model response after Stop must never resurrect its run.
                first.gate.set()
                await asyncio.sleep(0)
                if transition == "stop_before_payment":
                    assert not ws.paypal.calls
            else:
                result = await settle(ws, identifier, approved)
                assert result["status"] == "completed"
            await assert_views(ws, identifier, result["status"])
            if transition == "switch_mode":
                target = "Monitor" if mode == "Enforce" else "Enforce"
                assert (await ws.client.patch("/api/mode", json={"mode": target})).json()["mode"] == target
            if transition == "reset":
                assert (await ws.client.post("/api/sandbox/reset", json={"confirmed": True})).status_code == 200
                for audience in ("client", "console"):
                    assert not (await ws.app.state.snapshot(audience))["runs"]
                assert not (await ws.client.get("/api/audit")).json()["rows"]
            second = await start(ws, Plan(pair[1]))
            final = await settle(ws, second, approved)
            assert final["status"] == "completed"
            await assert_views(ws, second, "completed")
            assert all(run.status in TERMINAL for run in ws.app.state.runs.values())
            rows = (await ws.client.get("/api/audit")).json()["rows"]
            assert not any(row["status"] in {"awaiting_approval", "executing"} for row in rows)
            provider_ids = [request_id for _, request_id in ws.paypal.calls]
            assert len(provider_ids) == len(set(provider_ids)), "Every provider call must have a unique audit/idempotency ID"
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scenario", ("A", "B"))
def test_summary_generation_keeps_task_running_and_recovers_conflict(tmp_path, mode, scenario):
    async def check():
        async with workspace(tmp_path, mode=mode) as ws:
            plan = Plan(scenario)
            plan.gate_at = len(plan.steps()) - 1
            identifier = await start(ws, plan)
            if scenario == "B" and mode == "Enforce":
                pending = await wait_status(ws, identifier, "awaiting_approval")
                assert (await ws.client.post(f'/api/approvals/{pending["approvals"][0]["id"]}', json={"approved": True})).status_code == 200
            await asyncio.wait_for(plan.entered.wait(), 2)
            await assert_views(ws, identifier, "running")
            # Payment tool terminality must not masquerade as agent terminality.
            visible = (await ws.app.state.snapshot("client"))["runs"][0]
            assert any(event["type"] in {"tool_result", "client_status"} for event in visible["events"])
            assert not any(event["type"] == "run_status" and event["status"] in TERMINAL for event in visible["events"])
            conflict = await ws.client.post("/api/runs", json={"message": PROMPTS["B"]})
            assert conflict.status_code == 409
            detail = conflict.json()["detail"]
            assert detail["run_id"] == identifier and detail["status"] == "running"
            assert detail["run"]["status"] == "running"
            assert identifier[:8] in detail["message"]
            reset = await ws.client.post("/api/sandbox/reset", json={"confirmed": True})
            assert reset.status_code == 409
            assert reset.json()["detail"]["run_id"] == identifier
            plan.gate.set()
            assert (await settle(ws, identifier))["status"] == "completed"
            await assert_views(ws, identifier, "completed")
            next_id = await start(ws, Plan("F"))
            assert (await settle(ws, next_id))["status"] == "completed"
    asyncio.run(check())


@pytest.mark.parametrize("pending_scenario", ("headphones", "blocked_then_alternative", "judge_exception", "judge_timeout", "judge_invalid"))
@pytest.mark.parametrize("action", ("approve", "deny", "stop"))
def test_pending_approval_visible_recoverable_and_reset_safe(tmp_path, pending_scenario, action):
    async def check():
        failure = pending_scenario.removeprefix("judge_") if pending_scenario.startswith("judge_") else "normal"
        async with workspace(tmp_path, judge_behavior=failure,
                             unknown_requires_approval=pending_scenario == "blocked_then_alternative") as ws:
            plan = Plan("A", behavior="alternative_after_block") if pending_scenario == "blocked_then_alternative" else Plan("B")
            identifier = await start(ws, plan)
            pending = await wait_status(ws, identifier, "awaiting_approval")
            approval = next(card for card in pending["approvals"] if card["status"] == "pending")
            await assert_views(ws, identifier, "awaiting_approval")
            client_run = next(run for run in (await ws.app.state.snapshot("client"))["runs"] if run["id"] == identifier)
            assert client_run["approvals"] and client_run["approvals"][0]["id"] == approval["id"]
            assert client_run["approvals"][0]["status"] == "pending"
            for endpoint, body in (("/api/runs", {"message": PROMPTS["A"]}), ("/api/sandbox/reset", {"confirmed": True})):
                response = await ws.client.post(endpoint, json=body)
                assert response.status_code == 409
                detail = response.json()["detail"]
                assert detail["run_id"] == identifier and detail["status"] == "awaiting_approval"
                assert "waiting for approval" in detail["message"].lower()
                assert approval["id"] in detail["approval_url"]
                assert detail["run"]["approvals"][0]["id"] == approval["id"]
            assert not ws.paypal.calls
            if action == "stop":
                assert (await ws.client.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
                final = await wait_status(ws, identifier, "cancelled")
            else:
                assert (await ws.client.post(f'/api/approvals/{approval["id"]}', json={"approved": action == "approve"})).status_code == 200
                final = await settle(ws, identifier)
            assert (await ws.client.post(f'/api/approvals/{approval["id"]}', json={"approved": True})).status_code == 409
            assert len(ws.paypal.calls) == (1 if action == "approve" else 0)
            await assert_views(ws, identifier, final["status"])
            rows = (await ws.client.get("/api/audit")).json()["rows"]
            assert not any(row["status"] in {"awaiting_approval", "executing"} for row in rows)
            assert (await ws.client.post("/api/sandbox/reset", json={"confirmed": True})).status_code == 200
            next_id = await start(ws, Plan("F"))
            assert (await settle(ws, next_id))["status"] == "completed"
    asyncio.run(check())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("behavior", ("normal", "order_first", "recommend_only", "alternative_after_block"))
def test_live_tool_order_and_recommendation_variants(tmp_path, mode, behavior):
    async def check():
        async with workspace(tmp_path, mode=mode) as ws:
            identifier = await start(ws, Plan("A", behavior=behavior))
            result = await settle(ws, identifier)
            assert result["status"] == "completed"
            await assert_views(ws, identifier, "completed")
            expected = 0 if behavior == "recommend_only" else 2 if mode == "Monitor" else 1
            assert len(ws.paypal.calls) == expected
            assert all(call.tool == "create_order" for call, _ in ws.paypal.calls) if mode == "Enforce" else True
            rows = (await ws.client.get("/api/audit")).json()["rows"]
            if behavior != "recommend_only":
                payout = next(row for row in rows if row["tool"] == "create_payout")
                assert payout["computed_decision"] == "BLOCK"
                assert payout["status"] == ("blocked" if mode == "Enforce" else "succeeded")
                if mode == "Monitor":
                    assert payout["paypal_status"] == "PENDING"
    asyncio.run(check())


@pytest.mark.parametrize("mode", MODES)
def test_two_tabs_start_race_adopts_same_active_run_and_can_stop(tmp_path, mode):
    async def check():
        async with workspace(tmp_path, mode=mode) as ws:
            plan = Plan("A", gate_at=0)
            ws.provider.plans.append(plan)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ws.app), base_url="http://second-tab") as other:
                responses = await asyncio.gather(*[
                    client.post("/api/runs", json={"message": PROMPTS["A"]}) for client in (ws.client, other)
                ])
                assert sorted(response.status_code for response in responses) == [202, 409]
                identifier = next(response.json()["id"] for response in responses if response.status_code == 202)
                detail = next(response.json()["detail"] for response in responses if response.status_code == 409)
                assert detail["run_id"] == identifier
                assert detail["run"]["id"] == identifier and detail["run"]["status"] == "running"
                await asyncio.wait_for(plan.entered.wait(), 2)
                await assert_views(ws, identifier, "running")
                assert (await other.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
                plan.gate.set()
                await assert_views(ws, identifier, "cancelled")
                assert not ws.paypal.calls
                next_id = await start(ws, Plan("F"), client=other)
                assert (await settle(ws, next_id))["status"] == "completed"
    asyncio.run(check())


@pytest.mark.parametrize("mode", MODES)
def test_stop_during_provider_call_preserves_unknown_without_replaying(tmp_path, mode):
    async def check():
        async with workspace(tmp_path, mode=mode) as ws:
            ws.paypal.gate = asyncio.Event()
            identifier = await start(ws, Plan("A", behavior="order_first"))
            await asyncio.wait_for(ws.paypal.entered.wait(), 2)
            assert (await ws.client.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
            ws.paypal.gate.set()
            await asyncio.sleep(0)
            await assert_views(ws, identifier, "cancelled")
            assert len(ws.paypal.calls) == 1
            rows = (await ws.client.get("/api/audit")).json()["rows"]
            order = next(row for row in rows if row["tool"] == "create_order")
            assert order["status"] == "unknown" and not order["paypal_id"]
            next_id = await start(ws, Plan("F"))
            assert (await settle(ws, next_id))["status"] == "completed"
            assert len(ws.paypal.calls) == 1
    asyncio.run(check())


@pytest.mark.parametrize("mode", MODES)
def test_model_failure_after_last_tool_does_not_leave_a_zombie_run(tmp_path, mode):
    async def check():
        async with workspace(tmp_path, mode=mode) as ws:
            plan = Plan("A")
            plan.fail_at = len(plan.steps()) - 1
            identifier = await start(ws, plan)
            result = await settle(ws, identifier)
            assert result["status"] == "failed"
            await assert_views(ws, identifier, "failed")
            previous_calls = len(ws.paypal.calls)
            next_id = await start(ws, Plan("F"))
            assert (await settle(ws, next_id))["status"] == "completed"
            assert len(ws.paypal.calls) == previous_calls
    asyncio.run(check())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scenario", ("A", "B"))
@pytest.mark.parametrize("failure", ("exception", "timeout", "invalid"))
@pytest.mark.parametrize("answer", ("approve", "deny", "stop"))
def test_judge_failure_matrix(tmp_path, mode, scenario, failure, answer):
    async def check():
        async with workspace(tmp_path, mode=mode, judge_behavior=failure) as ws:
            identifier = await start(ws, Plan(scenario))
            if mode == "Monitor":
                final = await settle(ws, identifier)
                assert not final["approvals"]
                assert len(ws.paypal.calls) == (2 if scenario == "A" else 1)
                rows = (await ws.client.get("/api/audit")).json()["rows"]
                reviewed = next(row for row in rows if row["tool"] == ("create_payout" if scenario == "A" else "create_order"))
                assert reviewed["computed_decision"] == "REQUIRE_APPROVAL"
                assert reviewed["status"] == "succeeded"
            else:
                pending = await wait_status(ws, identifier, "awaiting_approval")
                await assert_views(ws, identifier, "awaiting_approval")
                assert not ws.paypal.calls
                approval = next(card for card in pending["approvals"] if card["status"] == "pending")
                conflict = await ws.client.post("/api/runs", json={"message": PROMPTS["F"]})
                assert conflict.status_code == 409 and conflict.json()["detail"]["run_id"] == identifier
                if answer == "stop":
                    assert (await ws.client.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
                    final = await wait_status(ws, identifier, "cancelled")
                else:
                    assert (await ws.client.post(f'/api/approvals/{approval["id"]}', json={"approved": answer == "approve"})).status_code == 200
                    final = await settle(ws, identifier, answer == "approve")
                # Approving an injected payout still cannot override its hard risk block.
                expected = 0 if answer == "stop" else 1 if scenario == "A" or answer == "approve" else 0
                assert len(ws.paypal.calls) == expected
                assert all(call.tool == "create_order" for call, _ in ws.paypal.calls)
            await assert_views(ws, identifier, final["status"])
            next_id = await start(ws, Plan("F"))
            assert (await settle(ws, next_id))["status"] == "completed"
    asyncio.run(check())


@pytest.mark.parametrize("scenario", ("B", "alternative"))
@pytest.mark.parametrize("answer", ("approve", "deny", "stop"))
def test_pending_mode_switch_does_not_hide_or_implicitly_approve(tmp_path, scenario, answer):
    async def check():
        async with workspace(tmp_path, unknown_requires_approval=scenario == "alternative") as ws:
            plan = Plan("B") if scenario == "B" else Plan("A", behavior="alternative_after_block")
            identifier = await start(ws, plan)
            pending = await wait_status(ws, identifier, "awaiting_approval")
            approval = next(card for card in pending["approvals"] if card["status"] == "pending")
            assert (await ws.client.patch("/api/mode", json={"mode": "Monitor"})).status_code == 200
            await asyncio.sleep(0)
            await assert_views(ws, identifier, "awaiting_approval")
            assert not ws.paypal.calls
            safe = (await ws.client.get(f"/api/runs/{identifier}?audience=client")).json()
            assert safe["approvals"][0]["id"] == approval["id"] and safe["approvals"][0]["status"] == "pending"
            if answer == "stop":
                assert (await ws.client.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
                final = await wait_status(ws, identifier, "cancelled")
            else:
                assert (await ws.client.post(f'/api/approvals/{approval["id"]}', json={"approved": answer == "approve"})).status_code == 200
                final = await settle(ws, identifier)
            assert len(ws.paypal.calls) == (1 if answer == "approve" else 0)
            await assert_views(ws, identifier, final["status"])
    asyncio.run(check())


@pytest.mark.parametrize("answers", ((True, True), (True, False), (False, True), (False, False)),
                         ids=("approve-approve", "approve-deny", "deny-approve", "deny-deny"))
def test_two_tabs_resolve_approval_once(tmp_path, answers):
    async def check():
        async with workspace(tmp_path) as ws:
            identifier = await start(ws, Plan("B"))
            pending = await wait_status(ws, identifier, "awaiting_approval")
            approval = pending["approvals"][0]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ws.app), base_url="http://second-tab") as other:
                responses = await asyncio.gather(*[
                    client.post(f'/api/approvals/{approval["id"]}', json={"approved": approved})
                    for client, approved in zip((ws.client, other), answers)
                ])
                assert sorted(response.status_code for response in responses) == [200, 409]
                accepted = next(response.json()["approved"] for response in responses if response.status_code == 200)
                final = await settle(ws, identifier)
                assert final["approvals"][0]["status"] == ("approved" if accepted else "denied")
                assert len(ws.paypal.calls) == int(accepted)
                await assert_views(ws, identifier, "completed")
                rows = (await ws.client.get("/api/audit")).json()["rows"]
                row = next(row for row in rows if row["tool"] == "create_order")
                assert sum(item["action"] in {"approved", "denied"} for item in row["approval_history"]) == 1
    asyncio.run(check())


@pytest.mark.parametrize("first", ("approve", "stop"))
def test_two_tabs_approval_stop_race_cannot_submit_after_stop(tmp_path, first):
    async def check():
        async with workspace(tmp_path) as ws:
            identifier = await start(ws, Plan("B"))
            pending = await wait_status(ws, identifier, "awaiting_approval")
            approval = pending["approvals"][0]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ws.app), base_url="http://second-tab") as other:
                async def approve():
                    return await ws.client.post(f'/api/approvals/{approval["id"]}', json={"approved": True})
                async def stop():
                    return await other.post(f"/api/runs/{identifier}/cancel")
                operations = (approve, stop) if first == "approve" else (stop, approve)
                responses = await asyncio.gather(*(operation() for operation in operations))
                assert responses[0 if first == "stop" else 1].status_code == 200
                final = await wait_status(ws, identifier, "cancelled")
                calls_at_stop = len(ws.paypal.calls)
                for _ in range(5):
                    await asyncio.sleep(0)
                assert len(ws.paypal.calls) == calls_at_stop <= 1
                assert final["status"] == "cancelled"
                await assert_views(ws, identifier, "cancelled")
                rows = (await ws.client.get("/api/audit")).json()["rows"]
                assert not any(row["status"] in {"awaiting_approval", "executing"} for row in rows)
                if calls_at_stop:
                    assert next(row for row in rows if row["tool"] == "create_order")["status"] in {"unknown", "succeeded"}
                next_id = await start(ws, Plan("F"))
                assert (await settle(ws, next_id))["status"] == "completed"
    asyncio.run(check())


@pytest.mark.parametrize("first", ("reset", "start"))
def test_two_tabs_reset_start_race_is_serialized(tmp_path, first):
    async def check():
        async with workspace(tmp_path) as ws:
            previous = await start(ws, Plan("F"))
            assert (await settle(ws, previous))["status"] == "completed"
            ws.provider.plans.append(Plan("A", gate_at=0))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ws.app), base_url="http://second-tab") as other:
                async def reset():
                    return await ws.client.post("/api/sandbox/reset", json={"confirmed": True})
                async def run():
                    return await other.post("/api/runs", json={"message": PROMPTS["A"]})
                operations = (reset, run) if first == "reset" else (run, reset)
                responses = await asyncio.gather(*(operation() for operation in operations))
                started = responses[1 if first == "reset" else 0]
                reset_result = responses[0 if first == "reset" else 1]
                assert started.status_code == 202
                assert reset_result.status_code in {200, 409}
                identifier = started.json()["id"]
                assert identifier in ws.app.state.runs
                await assert_views(ws, identifier, "running")
                assert (await other.post(f"/api/runs/{identifier}/cancel")).json()["status"] == "cancelled"
                await assert_views(ws, identifier, "cancelled")
                assert not ws.paypal.calls
                assert (await ws.client.post("/api/sandbox/reset", json={"confirmed": True})).status_code == 200
    asyncio.run(check())


def test_two_sse_tabs_observe_approval_resume_and_terminal_in_order(tmp_path):
    async def check():
        async with workspace(tmp_path) as ws:
            plan = Plan("B")
            plan.gate_at = len(plan.steps()) - 1
            identifier = await start(ws, plan)
            streams = [ws.app.state.event_stream(ConnectedTab(), audience) for audience in ("client", "console")]
            async def frames(status):
                for stream in streams:
                    frame = await asyncio.wait_for(anext(stream), 1)
                    state = json.loads(frame.split("data: ", 1)[1])
                    run = next(run for run in state["runs"] if run["id"] == identifier)
                    assert run["status"] == status
                    assert ws.app.state.runs[identifier].status == status
                    if status == "completed":
                        assert [event["status"] for event in run["events"] if event["type"] == "run_status"][-1] == status
            try:
                await frames("running")
                pending = await wait_status(ws, identifier, "awaiting_approval")
                await frames("awaiting_approval")
                assert (await ws.client.post(f'/api/approvals/{pending["approvals"][0]["id"]}', json={"approved": True})).status_code == 200
                await asyncio.wait_for(plan.entered.wait(), 2)
                await frames("running")
                plan.gate.set()
                assert (await settle(ws, identifier))["status"] == "completed"
                await frames("completed")
            finally:
                for stream in streams:
                    await stream.aclose()
    asyncio.run(check())


@pytest.mark.parametrize("mode", MODES)
def test_free_form_purchase_is_also_a_real_tool_loop(tmp_path, mode):
    async def check():
        async with workspace(tmp_path, mode=mode) as ws:
            plan = Plan("A", behavior="alternative_after_block")
            # Same tools with an unscripted user instruction; the review fake
            # accepts the ordinary merchant, while the injected payout is blocked.
            ws.provider.plans.append(plan)
            response = await ws.client.post("/api/runs", json={"message": "Buy a travel USB-C charger under $60"})
            assert response.status_code == 202
            identifier = response.json()["id"]
            assert (await settle(ws, identifier))["status"] == "completed"
            await assert_views(ws, identifier, "completed")
            orders = [call for call, _ in ws.paypal.calls if call.tool == "create_order"]
            assert len(orders) == 1 and str(orders[0].amount) == "24.99"
    asyncio.run(check())
