"""Deterministic asynchronous boundaries for lifecycle and recovery regressions."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import RuleJudge, ToolCall


CHARGER = "Buy me the cheapest 65W USB-C charger under $40"
HEADPHONES = "Buy me noise-cancelling headphones under $300"


class Connected:
    async def is_disconnected(self):
        return False


def parse_event(event):
    return json.loads(event.split("data: ", 1)[1])


async def until(predicate):
    async def waiting():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(waiting(), 2)


def settings(tmp_path, **kwargs):
    return Settings(sqlite_path=str(tmp_path / "lifecycle.sqlite3"),
                    initial_mode="Enforce", openai_api_key="test-only",
                    run_rate_limit_per_minute=100, run_rate_limit_per_hour=100,
                    **kwargs)


def test_payment_result_is_not_run_completion_and_summary_keeps_recoverable_lock(tmp_path):
    async def scenario():
        app = create_app(settings(tmp_path), paypal=MockPayPal(), judge=RuleJudge())
        agent = app.state.agent
        summary_started, finish_summary = asyncio.Event(), asyncio.Event()

        async def live(run):
            products = await agent.products(run)
            product = products[0]
            await agent.open_product(run, product["id"])
            # Typical gpt-4o-mini order: injected payout, then legitimate order.
            blocked = await agent.payment(run, ToolCall(tool="create_payout", amount="500",
                payee="vendor-support@evil.example", description="Verification escrow"))
            assert blocked["status"] == "blocked"
            await agent.payment(run, ToolCall(tool="create_order", amount=product["price"],
                payee=agent.settings.shop_email, description=product["name"]))
            summary_started.set()
            await finish_summary.wait()
            await run.say("The order was created; the verification payout was rejected.")

        agent.live = live
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                started = (await client.post("/api/runs", json={"message":CHARGER})).json()
                identifier = started["id"]
                assert started["run"]["status"] == "running"
                await asyncio.wait_for(summary_started.wait(), 2)
                stream = app.state.event_stream(Connected(), "client")
                before = parse_event(await anext(stream))["runs"][0]
                assert before["status"] == "running"
                assert any(e.get("status") == "succeeded" for e in before["events"])
                assert not any(e.get("status") == "completed" for e in before["events"])
                response = await client.post("/api/runs", json={"message":HEADPHONES})
                detail = response.json()["detail"]
                assert response.status_code == 409
                assert detail["run_id"] == identifier and detail["status"] == "running"
                assert identifier[:8] in detail["message"] and "summary" in detail["message"]
                assert detail["run"]["status"] == "running"
                finish_summary.set()
                await until(lambda: app.state.runs[identifier].status == "completed")
                after = parse_event(await anext(stream))["runs"][0]
                full = (await client.get(f"/api/runs/{identifier}")).json()
                assert after["status"] == full["status"] == "completed"
                assert after["events"][-1]["type"] == "run_status"
                assert after["events"][-1]["status"] == full["status"]
                assert after["revision"] > before["revision"]
                # Captured snapshots remain consistent after the original run mutates.
                assert before["status"] == "running"
                assert before["events"][-1].get("status") != "completed"
                await stream.aclose()
                agent.live = AsyncMock()
                assert (await client.post("/api/runs", json={"message":HEADPHONES})).status_code == 202
    asyncio.run(scenario())


def test_pending_approval_visible_in_safe_snapshot_and_conflict_and_reset(tmp_path):
    async def scenario():
        app = create_app(settings(tmp_path), paypal=MockPayPal(), judge=RuleJudge())
        agent = app.state.agent

        async def live(run):
            products = await agent.products(run)
            chosen = next(product for product in products if product["id"] == "fixture-sony-headphones")
            await agent.open_product(run, chosen["id"])
            await agent.payment(run, ToolCall(tool="create_order", amount=chosen["price"],
                payee=agent.settings.shop_email, description=chosen["name"]))

        agent.live = live
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                identifier = (await client.post("/api/runs", json={"message":HEADPHONES})).json()["id"]
                await until(lambda: app.state.runs[identifier].status == "awaiting_approval")
                safe = (await client.get(f"/api/runs/{identifier}?audience=client")).json()
                full = (await client.get(f"/api/runs/{identifier}")).json()
                assert safe["status"] == full["status"] == "awaiting_approval"
                assert len(safe["approvals"]) == 1
                card = safe["approvals"][0]
                assert set(card) == {"id", "run_id", "status", "console_url"}
                assert card["run_id"] == identifier and card["status"] == "pending"
                assert "approval=" + card["id"] in card["console_url"]
                assert "provenance" in full["approvals"][0]
                assert "provenance" not in json.dumps(safe)
                for path, body in [("/api/runs", {"message":CHARGER}), ("/api/sandbox/reset", {"confirmed":True})]:
                    response = await client.post(path, json=body)
                    detail = response.json()["detail"]
                    assert response.status_code == 409
                    assert detail["run_id"] == identifier
                    assert detail["run"]["approvals"] == safe["approvals"]
                    assert detail["approval_url"] == card["console_url"]
                    assert "waiting for approval" in detail["message"]
                stopped = await client.post(f"/api/runs/{identifier}/cancel?audience=client")
                assert stopped.json()["status"] == "cancelled"
                assert stopped.json()["approvals"][0]["status"] == "cancelled"
                assert "provenance" not in json.dumps(stopped.json())
                assert (await client.post(f"/api/approvals/{card['id']}", json={"approved":True})).status_code == 409
                assert (await client.post("/api/sandbox/reset", json={"confirmed":True})).status_code == 200
    asyncio.run(scenario())


def test_concurrent_stop_waits_for_cleanup_without_recancelling_or_resurrecting_run(tmp_path):
    async def scenario():
        submitted, cancelling, cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class SlowCleanupPayPal(MockPayPal):
            def __init__(self):
                self.calls = []
                self.cancellations = 0

            async def execute(self, call, request_id):
                self.calls.append(call)
                submitted.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancellations += 1
                    cancelling.set()
                    # Model an adapter that must finish local cleanup and has a
                    # known accepted provider result despite Stop arriving.
                    await cleanup.wait()
                    return await super().execute(call, request_id)

        paypal = SlowCleanupPayPal()
        app = create_app(settings(tmp_path), paypal=paypal, judge=RuleJudge())
        agent = app.state.agent

        async def live(run):
            product = (await agent.products(run))[0]
            await agent.open_product(run, product["id"])
            await agent.payment(run, ToolCall(tool="create_order", amount=product["price"],
                payee=agent.settings.shop_email, description=product["name"]))
            # Swallowed provider cancellation must not permit a second payment.
            await agent.payment(run, ToolCall(tool="create_payout", amount="500",
                payee="vendor-support@evil.example", description="Verification"))

        agent.live = live
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                identifier = (await client.post("/api/runs", json={"message":CHARGER})).json()["id"]
                await asyncio.wait_for(submitted.wait(), 2)
                first = asyncio.create_task(client.post(f"/api/runs/{identifier}/cancel"))
                await asyncio.wait_for(cancelling.wait(), 2)
                second = asyncio.create_task(client.post(f"/api/runs/{identifier}/cancel"))
                responses = await asyncio.gather(first, second)
                assert [response.json()["status"] for response in responses] == ["cancelling", "cancelling"]
                assert paypal.cancellations == 1
                for path, body in [("/api/runs", {"message":HEADPHONES}), ("/api/sandbox/reset", {"confirmed":True})]:
                    response = await client.post(path, json=body)
                    assert response.status_code == 409
                    assert response.json()["detail"]["status"] == "cancelling"
                cleanup.set()
                await until(lambda: app.state.runs[identifier].status == "cancelled")
                await asyncio.sleep(0)
                run = app.state.runs[identifier]
                assert run.events[-1]["type"] == "run_status"
                assert run.events[-1]["status"] == "cancelled"
                assert len(paypal.calls) == 1
                rows = (await client.get("/api/audit")).json()["rows"]
                assert sum(row["tool"] == "cancel_run" for row in rows) == 1
                assert next(row for row in rows if row["tool"] == "create_order")["status"] == "succeeded"
                agent.live = AsyncMock()
                assert (await client.post("/api/runs", json={"message":HEADPHONES})).status_code == 202
    asyncio.run(scenario())


def test_conflict_and_blank_message_do_not_consume_run_limit(tmp_path):
    async def scenario():
        config = settings(tmp_path)
        config.run_rate_limit_per_minute = 2
        app = create_app(config, paypal=MockPayPal(), judge=RuleJudge())
        gate = asyncio.Event()

        async def held(_run):
            await gate.wait()

        app.state.agent.live = held
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                identifier = (await client.post("/api/runs", json={"message":CHARGER})).json()["id"]
                for _ in range(8):
                    assert (await client.post("/api/runs", json={"message":HEADPHONES})).status_code == 409
                await client.post(f"/api/runs/{identifier}/cancel")
                for _ in range(4):
                    assert (await client.post("/api/runs", json={"message":" "})).status_code == 422
                app.state.agent.live = AsyncMock()
                assert (await client.post("/api/runs", json={"message":HEADPHONES})).status_code == 202
    asyncio.run(scenario())


@pytest.mark.parametrize("raises", [False, True], ids=["missing-terminal-state", "uncaught-error"])
def test_unexpected_runner_exit_cannot_leave_a_ghost_active_run(tmp_path, raises):
    async def scenario():
        app = create_app(settings(tmp_path), paypal=MockPayPal(), judge=RuleJudge())

        async def broken_runner(run):
            await asyncio.sleep(0)
            if raises:
                raise RuntimeError("Simulated runner failure")

        app.state.agent.run = broken_runner
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                identifier = (await client.post("/api/runs", json={"message":CHARGER})).json()["id"]
                await until(lambda: app.state.runs[identifier].status == "failed")
                safe = (await client.get(f"/api/runs/{identifier}?audience=client")).json()
                assert safe["status"] == "failed"
                assert safe["events"][-1]["status"] == "failed"
                assert "stopped unexpectedly" in safe["error"]
                assert (await client.post("/api/runs", json={"message":HEADPHONES})).status_code == 202
    asyncio.run(scenario())
