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
        set_mode(body.mode)
        if old != mode:
            audit.record_config_change("mode", old, mode)
        return {"mode":mode}

    @app.get("/api/policies")
    async def get_policies():
        return firewall.policy.model_dump(mode="json")

    @app.patch("/api/policies")
    async def update_policies(body: dict):
        nonlocal policy
        before = firewall.policy.model_dump(mode="json")
        try:
            updated = Policy.model_validate({**before, **body})
        except ValidationError:
            raise HTTPException(422, "Invalid policy configuration") from None
        policy = firewall.policy = updated
        after = updated.model_dump(mode="json")
        audit.record_config_change("policy", before, after)
        return after

    def wallet_snapshot():
        # Ledger movement reflects accepted calls/created orders, not settlement.
        rows = audit.report()["rows"]
        amount = sum((Decimal(str(row["amount"])) for row in rows if row["status"] == "succeeded" and row["tool"] in {"create_order", "create_payout"}), Decimal(0))
        return {"balance":f"{settings.sandbox_starting_balance-amount:.2f}", "currency":"USD", "label":"Sandbox · local balance", "source":"local_ledger", "starting_balance":f"{settings.sandbox_starting_balance:.2f}", "paypal_mode":paypal.mode}

    @app.post("/api/sandbox/reset")
    async def reset_sandbox_data(body: ResetSandboxData):
        if any(run.status in {"running", "awaiting_approval"} for run in runs.values()) or any(not task.done() for task in tasks) or approvals.pending:
            raise HTTPException(409, "Finish or stop the current run before resetting sandbox data")
        # No await in the check/reset section: a new run cannot interleave.
        audit.reset_sandbox_data()
        runs.clear()
        run_tasks.clear()
        approvals.pending.clear()
        firewall._reviews.clear()
        return {"status":"reset", "message":"Local sandbox data reset. PayPal sandbox orders and payouts are not reversed.", "wallet":wallet_snapshot()}

    @app.get("/api/wallet")
    async def get_wallet():
        return wallet_snapshot()

    async def state_snapshot(audience="console"):
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
        ip = client_ip(request)
        retry_after = None if ip in settings.rate_limit_exempt_ips else rate_limiter.check(ip)
        if retry_after is not None:
            raise HTTPException(429, f"Too many demo runs from your IP. Please try again in {retry_after} seconds.", headers={"Retry-After": str(retry_after)})
        if not body.message.strip():
            raise HTTPException(422,"Message cannot be blank")
        if any(run.status in {"running","awaiting_approval"} for run in runs.values()):
            raise HTTPException(409,"Finish the current demo run before starting another")
        requested = body.agent_mode or ("compromised" if settings.demo_mode else settings.agent_mode)
        if body.firewall_enabled is not None:
            # Legacy API compatibility: a requested mode changes the shared console state.
            previous_mode = mode_state()
            next_mode = "Enforce" if body.firewall_enabled else "Monitor"
            set_mode(next_mode)
            if previous_mode != next_mode:
                audit.record_config_change("mode", previous_mode, next_mode)
        run = Run(body.message,mode_state() == "Enforce",agent_mode=requested)
        runs[run.id] = run
        task = asyncio.create_task(agent.run(run))
        tasks.add(task)
        run_tasks[run.id] = task
        def finished(done):
            tasks.discard(done)
            run_tasks.pop(run.id, None)
        task.add_done_callback(finished)
        return {"id":run.id}

    @app.get("/api/runs/{identifier}")
    async def get_run(identifier: str):
        if identifier not in runs:
            raise HTTPException(404,"Run not found")
        run = runs[identifier]
        # Runs retain their actual agent mode; the judge badge tracks the current fallback.
        modes = runtime_modes()
        if run.status in {"running", "awaiting_approval"}:
            run.judge_mode, run.llm_degraded, run.judge_model = modes["judge_mode"], modes["llm_degraded"], modes["judge_model"]
        return run.snapshot()

    @app.post("/api/runs/{identifier}/cancel")
    async def cancel_run(identifier: str):
        run = runs.get(identifier)
        if run is None:
            raise HTTPException(404,"Run not found")
        if run.status in {"running", "awaiting_approval"}:
            # No await before cancellation: approval resolution cannot interleave here.
            run.status = "cancelled"
            approvals.cancel_run(run)
            audit.record_run_cancellation(run.id, run.firewall_enabled, paypal.mock)
            task = run_tasks.get(identifier)
            if task is not None:
                task.cancel()
            await run.say("Run cancelled. Submitted PayPal calls are not rolled back; check the sandbox before retrying a payment.")
            await run.emit({"type":"cancelled", "status":"cancelled", "message":"Run cancelled by user."})
        return run.snapshot()

    @app.post("/api/approvals/{identifier}")
    async def resolve_approval(identifier: str,body: ApprovalChoice):
        if not approvals.resolve(identifier,body.approved):
            raise HTTPException(409,"Approval is absent or already resolved")
        audit.record_approval(identifier, "approved" if body.approved else "denied", actor="Sandbox console operator (unauthenticated)")
        return {"id":identifier,"approved":body.approved}

    @app.get("/api/audit")
    async def get_audit():
        return audit.report()

    @app.get("/api/audit/flagged")
    async def get_flagged(type: Literal["BLOCK", "REQUIRE_APPROVAL"] | None = None, policy: str | None = None):
        if policy is not None and policy not in POLICY_IDS:
            raise HTTPException(422, "Unknown policy")
        return audit.flagged(type, policy)

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
