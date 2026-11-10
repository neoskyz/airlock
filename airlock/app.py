import asyncio
import json
import os
from decimal import Decimal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
import yaml
from firewall import AuditStore, Firewall, Policy, RuleJudge, build_judge
from firewall.limits import DailyLLMBudget
from firewall.policy import POLICY_IDS
from .limits import RunRateLimiter, client_ip
from .agent import DemoAgent
from .catalog import Channel3Catalog, marketplace_listing, normalize_local
from .config import Settings
from .paypal import build_paypal
from .store import ApprovalManager, Run


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1,max_length=4000)
    firewall_enabled: bool | None = None
    agent_mode: Literal["live", "compromised"] | None = None


class ModeChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["Monitor", "Enforce"]


class ResetSandboxData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if value is not True:
            raise ValueError("Reset requires confirmed: true")
        return value


class ApprovalChoice(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    approved: bool


def create_app(settings=None, *, paypal=None, judge=None, catalog=None):
    settings = settings or Settings.from_env()
    policy = Policy.load(settings.policy_path)
    policy.allowed_payees = [settings.shop_email if x == "shop@chargers.example" else x for x in policy.allowed_payees]
    audit = AuditStore(settings.sqlite_path)
    paypal = paypal or build_paypal(settings.paypal_client_id,settings.paypal_client_secret)
    budget = DailyLLMBudget(settings.daily_llm_call_limit)
    rate_limiter = RunRateLimiter(settings.run_rate_limit_per_minute, settings.run_rate_limit_per_hour)
    fallback = RuleJudge(settings.shop_email)
    judge = judge or build_judge(settings.anthropic_api_key, model=settings.judge_model, budget=budget, fallback=fallback)
    if isinstance(judge, RuleJudge):
        judge.shop_payee = settings.shop_email
    if hasattr(judge,"trusted_contacts"):
        judge.trusted_contacts = {"shop":settings.shop_email}
    firewall = Firewall(policy,judge,audit)
    approvals = ApprovalManager()
    mode = settings.initial_mode
    catalog = catalog or Channel3Catalog(settings.channel3_api_key)
    agent = DemoAgent(settings,firewall,paypal,approvals,budget=budget,catalog=catalog,mode_getter=lambda:mode)
    runs, tasks, run_tasks = {}, set(), {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await paypal.close()
        await catalog.close()
        audit.close()

    app = FastAPI(title="Airlock",version="0.2.0",lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.llm_budget, app.state.rate_limiter = budget, rate_limiter
    app.state.audit, app.state.runs, app.state.agent = audit,runs,agent
    app.state.firewall = firewall

    @app.get("/api/health")
    async def health():
        return {"status":"ok"}

    def runtime_modes():
        degraded = budget.exhausted
        return {**budget.snapshot(), "agent_mode": "compromised" if degraded else settings.resolve_agent_mode(),
                "live_available": bool(settings.agent_api_key) and not degraded,
                "judge_mode": "rules" if degraded else judge.mode,
                "judge_model": "rules (daily LLM limit reached)" if degraded else getattr(judge,"model",settings.judge_model) if judge.mode == "anthropic" else "rules (no API key)"}

    @app.get("/api/config")
    async def config():
        modes = runtime_modes()
        return {"mode":mode, "version":"0.2.0", "deployed_on_render":os.getenv("RENDER", "").lower() == "true",
                "demo_mode": modes["agent_mode"] == "compromised", "paypal_mode": paypal.mode,
                "agent_provider": settings.agent_provider, "agent_model": settings.agent_model,
                "policy_yaml": yaml.safe_dump(policy.model_dump(mode="json"), sort_keys=False),
                "shop_email": settings.shop_email,
                **modes}

    def mode_state():
        return mode

    def set_mode(value):
        nonlocal mode
        mode = value

    @app.get("/api/mode")
    async def get_mode():
        return {"mode":mode}

    @app.patch("/api/mode")
    async def update_mode(body: ModeChoice):
        old = mode
        if old != body.mode:
            audit.record_config_change("mode", old, body.mode)
            set_mode(body.mode)
        return {"mode":mode}

    @app.get("/api/policies")
    async def get_policies():
        return firewall.policy.model_dump(mode="json")

    @app.patch("/api/policies")
    async def update_policies(body: dict):
        nonlocal policy
        before = firewall.policy.model_dump(mode="json")
        try:
            changes = dict(body)
            if isinstance(changes.get("enabled"), dict):
                changes["enabled"] = {**before["enabled"], **changes["enabled"]}
            if isinstance(changes.get("risk_weights"), dict):
                changes["risk_weights"] = {**before["risk_weights"], **changes["risk_weights"]}
            if "allowed_payees" in changes:
                import re
                recipients = changes["allowed_payees"]
                address = re.compile(r"(?:[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
                if not isinstance(recipients, list) or any(not isinstance(value, str) or len(value) > 254 or not address.fullmatch(value) for value in recipients):
                    raise HTTPException(422, "Recipients must be email addresses or domains")
                if len({value.lower() for value in recipients}) != len(recipients):
                    raise HTTPException(422, "Duplicate recipient")
            updated = Policy.model_validate({**before, **changes})
            if updated.currency != "USD":
                raise HTTPException(422, "The sandbox wallet supports USD policies only")
        except ValidationError:
            raise HTTPException(422, "Invalid policy configuration") from None
        after = updated.model_dump(mode="json")
        audit.record_config_change("policy", before, after)
        policy = firewall.policy = updated
        return after

    wallet_baseline = {"balance": None, "spent_at_fetch": Decimal(0), "tried": False}

    def ledger_spent():
        # Ledger movement reflects accepted calls/created orders, not settlement.
        rows = audit.report()["rows"]
        return sum((Decimal(str(row["amount"])) for row in rows if row["status"] == "succeeded" and row["tool"] in {"create_order", "create_payout"}), Decimal(0))

    async def refresh_wallet_baseline():
        wallet_baseline["tried"] = True
        fetch = getattr(paypal, "balance", None)
        if fetch is None:
            return
        try:
            real = await fetch()
        except Exception:
            real = None
        if real is not None:
            wallet_baseline.update(balance=real, spent_at_fetch=ledger_spent())

    def wallet_snapshot():
        spent = ledger_spent()
        if wallet_baseline["balance"] is not None:
            # Real PayPal balance at the last refresh, minus payments accepted since (PayPal's reporting API lags).
            balance = wallet_baseline["balance"] - (spent - wallet_baseline["spent_at_fetch"])
            return {"balance":f"{balance:.2f}", "currency":"USD", "label":"PayPal sandbox balance", "source":"paypal", "starting_balance":f"{wallet_baseline['balance']:.2f}", "paypal_mode":paypal.mode}
        return {"balance":f"{settings.sandbox_starting_balance-spent:.2f}", "currency":"USD", "label":"Sandbox · local balance", "source":"local_ledger", "starting_balance":f"{settings.sandbox_starting_balance:.2f}", "paypal_mode":paypal.mode}

    def blocking_run():
        # Cancelled tasks can still be unwinding a provider operation. Do not
        # overlap the next run with their cleanup or clear its audit ledger.
        return next((run for run in runs.values()
                     if run.status in {"running", "awaiting_approval", "cancelling"}
                     or (run.id in run_tasks and not run_tasks[run.id].done())), None)

    def run_conflict(run):
        pending = next((card for card in run.client_snapshot()["approvals"] if card["status"] == "pending"), None)
        if run.status == "awaiting_approval":
            message = "A previous task is waiting for approval in the Airlock console. Review its approval or stop the task."
        elif run.status == "cancelling":
            message = "A previous task is stopping. Wait for its payment cleanup before starting another task."
        else:
            message = "A previous task is still running, including any final agent summary. Wait for it or stop the task."
        detail = {"message":f"Task {run.id[:8]} ({run.status.replace('_', ' ')}): {message}", "run_id":run.id, "status":run.status, "run":run.client_snapshot()}
        if pending:
            detail["approval_url"] = pending["console_url"]
        return HTTPException(409, detail)

    @app.post("/api/sandbox/reset")
    async def reset_sandbox_data(body: ResetSandboxData):
        active_run = blocking_run()
        if active_run:
            raise run_conflict(active_run)
        if approvals.pending:
            raise HTTPException(409, "Pending payment cleanup must finish before resetting sandbox data")
        # No await in the check/reset section: a new run cannot interleave.
        audit.reset_sandbox_data()
        runs.clear()
        run_tasks.clear()
        approvals.pending.clear()
        firewall._reviews.clear()
        await refresh_wallet_baseline()
        return {"status":"reset", "message":"Local sandbox data reset. PayPal sandbox orders and payouts are not reversed.", "wallet":wallet_snapshot()}

    @app.get("/api/wallet")
    async def get_wallet():
        if not wallet_baseline["tried"]:
            await refresh_wallet_baseline()
        return wallet_snapshot()

    async def state_snapshot(audience="console"):
        if not wallet_baseline["tried"]:
            await refresh_wallet_baseline()
        full_config = await config()
        if audience == "client":
            full_config = {key:value for key,value in full_config.items() if key in {"paypal_mode", "demo_mode", "llm_degraded", "version"}}
        return {"config":full_config,
                "runs":[run.client_snapshot() if audience == "client" else run.snapshot() for run in runs.values()],
                "audit":{"rows":[],"stats":{"blocked_amount":0,"allowed_amount":0}} if audience == "client" else audit.report(),
                "wallet":wallet_snapshot()}

    async def event_stream(request, audience="console"):
        previous = None
        idle = 0
        while not await request.is_disconnected():
            current = json.dumps(await state_snapshot(audience), separators=(",", ":"), ensure_ascii=False)
            if current != previous:
                yield "event: state\ndata: " + current + "\n\n"
                previous = current
                idle = 0
            else:
                idle += 1
                if idle >= 150:
                    yield ": heartbeat\n\n"
                    idle = 0
            await asyncio.sleep(.1)

    app.state.event_stream = event_stream
    app.state.snapshot = state_snapshot

    @app.get("/api/events")
    async def events(request: Request, audience: Literal["client", "console"] = "console"):
        return StreamingResponse(event_stream(request, audience), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

    @app.post("/api/runs",status_code=202)
    async def start(body: StartRun, request: Request):
        if not body.message.strip():
            raise HTTPException(422,"Message cannot be blank")
        active_run = blocking_run()
        if active_run:
            raise run_conflict(active_run)
        ip = client_ip(request)
        retry_after = None if ip in settings.rate_limit_exempt_ips else rate_limiter.check(ip)
        if retry_after is not None:
            raise HTTPException(429, f"Too many demo runs from your IP. Please try again in {retry_after} seconds.", headers={"Retry-After": str(retry_after)})
        requested = body.agent_mode or ("compromised" if settings.demo_mode else settings.agent_mode)
        if body.firewall_enabled is not None:
            # Legacy API compatibility: a requested mode changes the shared console state.
            previous_mode = mode_state()
            next_mode = "Enforce" if body.firewall_enabled else "Monitor"
            if previous_mode != next_mode:
                audit.record_config_change("mode", previous_mode, next_mode)
                set_mode(next_mode)
        run = Run(body.message,mode_state() == "Enforce",agent_mode=requested)
        runs[run.id] = run
        task = asyncio.create_task(agent.run(run))
        tasks.add(task)
        run_tasks[run.id] = task
        def finished(done):
            tasks.discard(done)
            run_tasks.pop(run.id, None)
            # Last-resort lifecycle repair also covers cancellation before the
            # agent coroutine ever starts and unexpected adapter failures.
            if run.status == "cancelling":
                run.set_status("cancelled")
            elif run.status in {"running", "awaiting_approval"}:
                run.error = "Agent execution stopped unexpectedly. Check PayPal before retrying."
                approvals.cancel_run(run)
                run.set_status("failed")
            if not done.cancelled():
                done.exception()  # Retrieve failures without leaking task warnings.
        task.add_done_callback(finished)
        return {"id":run.id, "run":run.client_snapshot()}

    @app.get("/api/runs/{identifier}")
    async def get_run(identifier: str, audience: Literal["client", "console"] = "console"):
        if identifier not in runs:
            raise HTTPException(404,"Run not found")
        run = runs[identifier]
        # Runs retain their actual agent mode; the judge badge tracks the current fallback.
        modes = runtime_modes()
        if run.status in {"running", "awaiting_approval"}:
            run.judge_mode, run.llm_degraded, run.judge_model = modes["judge_mode"], modes["llm_degraded"], modes["judge_model"]
        return run.client_snapshot() if audience == "client" else run.snapshot()

    @app.post("/api/runs/{identifier}/cancel")
    async def cancel_run(identifier: str, audience: Literal["client", "console"] = "console"):
        run = runs.get(identifier)
        if run is None:
            raise HTTPException(404,"Run not found")
        was_active = run.status in {"running", "awaiting_approval"}
        task = run_tasks.get(identifier)
        if was_active or (task is not None and not task.done()):
            # No await before cancellation: approval resolution cannot interleave here.
            run.set_status("cancelling")
            approvals.cancel_run(run)
            if task is not None and not task.done():
                if was_active:
                    task.cancel()
                # A misbehaving adapter may suppress cancellation; keep the
                # workspace responsive and block new runs until it settles.
                await asyncio.wait({task}, timeout=.25)
            if task is None or task.done():
                run.set_status("cancelled")
            # A failed cancellation audit must not leave the agent running.
            if was_active:
                audit.record_run_cancellation(run.id, run.firewall_enabled, paypal.mock)
                await run.say("Run cancelled. Submitted PayPal calls are not rolled back; check the sandbox before retrying a payment.")
                if run.status == "cancelled":
                    await run.emit({"type":"cancelled", "status":"cancelled", "message":"Run cancelled by user."})
        return run.client_snapshot() if audience == "client" else run.snapshot()

    @app.post("/api/approvals/{identifier}")
    async def resolve_approval(identifier: str,body: ApprovalChoice):
        if not approvals.can_resolve(identifier):
            raise HTTPException(409,"Approval is absent or already resolved")
        # Persist the operator's choice before waking the payment task. There
        # is no await here, so cancellation and duplicate answers cannot race.
        audit.record_approval(identifier, "approved" if body.approved else "denied", actor="Sandbox console operator (unauthenticated)")
        approvals.resolve(identifier,body.approved)
        return {"id":identifier,"approved":body.approved}

    @app.get("/api/audit")
    async def get_audit():
        return audit.report()

    @app.get("/api/audit/flagged")
    async def get_flagged(type: Literal["BLOCK", "REQUIRE_APPROVAL"] | None = None, policy: str | None = None, reason_code: str | None = None):
        if policy is not None and policy not in POLICY_IDS:
            raise HTTPException(422, "Unknown policy")
        return audit.flagged(type, policy, reason_code)

    @app.get("/api/audit/{identifier}")
    async def get_audit_event(identifier: str):
        event = audit.event(identifier)
        if event is None:
            raise HTTPException(404, "Audit event not found")
        return event

    # The SPA never swallows missing API routes or nonexistent static assets.
    dist = Path(settings.frontend_dist)
    if (dist/"assets").is_dir():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/")
    async def root():
        return RedirectResponse("/console", status_code=307)

    @app.get("/api/store/volt-65w")
    async def volt_listing():
        return Channel3Catalog.page(normalize_local(marketplace_listing()))

    @app.get("/store/volt-65w")
    @app.get("/client")
    @app.get("/console")
    @app.get("/docs")
    @app.get("/console/flagged")
    @app.get("/console/events/{identifier}")
    async def index():
        if not (dist/"index.html").is_file():
            raise HTTPException(503,"Frontend not built. Run npm ci && npm run build in frontend/.")
        return FileResponse(dist/"index.html")

    return app


app = create_app()
