import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import yaml
from firewall import AuditStore, Firewall, Policy, RuleJudge, build_judge
from firewall.limits import DailyLLMBudget
from .limits import RunRateLimiter, client_ip
from .agent import DemoAgent
from .config import Settings
from .paypal import build_paypal
from .store import ApprovalManager, Run


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1,max_length=4000)
    firewall_enabled: bool = True
    agent_mode: Literal["live", "compromised"] | None = None


class ApprovalChoice(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    approved: bool


def create_app(settings=None, *, paypal=None, judge=None):
    settings = settings or Settings.from_env()
    policy = Policy.load(settings.policy_path)
    policy.allowed_payees = [settings.shop_email if x == "shop@chargers.example" else settings.contractor_email if x == "contractor@contractor.example" else x for x in policy.allowed_payees]
    audit = AuditStore(settings.sqlite_path)
    paypal = paypal or build_paypal(settings.paypal_client_id,settings.paypal_client_secret)
    budget = DailyLLMBudget(settings.daily_llm_call_limit)
    rate_limiter = RunRateLimiter(settings.run_rate_limit_per_minute, settings.run_rate_limit_per_hour)
    fallback = RuleJudge(settings.shop_email, settings.contractor_email)
    judge = judge or build_judge(settings.anthropic_api_key, model=settings.judge_model, budget=budget, fallback=fallback)
    if isinstance(judge, RuleJudge):
        judge.shop_payee, judge.contractor_payee = settings.shop_email,settings.contractor_email
    if hasattr(judge,"trusted_contacts"):
        judge.trusted_contacts = {"shop":settings.shop_email,"my contractor":settings.contractor_email}
    firewall = Firewall(policy,judge,audit)
    approvals = ApprovalManager()
    agent = DemoAgent(settings,firewall,paypal,approvals,budget=budget)
    runs, tasks = {}, set()

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await paypal.close()
        audit.close()

    app = FastAPI(title="Airlock",version="0.1.0",lifespan=lifespan)
    app.state.llm_budget, app.state.rate_limiter = budget, rate_limiter
    app.state.audit, app.state.runs, app.state.agent = audit,runs,agent

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
        return {"demo_mode": modes["agent_mode"] == "compromised", "paypal_mode": paypal.mode,
                "agent_provider": settings.agent_provider, "agent_model": settings.agent_model,
                "policy_yaml": yaml.safe_dump(policy.model_dump(mode="json"), sort_keys=False),
                "shop_email": settings.shop_email, "contractor_email": settings.contractor_email,
                **modes}

    @app.post("/api/runs",status_code=202)
    async def start(body: StartRun, request: Request):
        retry_after = rate_limiter.check(client_ip(request))
        if retry_after is not None:
            raise HTTPException(429, f"Too many demo runs from your IP. Please try again in {retry_after} seconds.", headers={"Retry-After": str(retry_after)})
        if not body.message.strip():
            raise HTTPException(422,"Message cannot be blank")
        if any(run.status in {"running","awaiting_approval"} for run in runs.values()):
            raise HTTPException(409,"Finish the current demo run before starting another")
        requested = body.agent_mode or ("compromised" if settings.demo_mode else settings.agent_mode)
        run = Run(body.message,body.firewall_enabled,agent_mode=requested)
        runs[run.id] = run
        task = asyncio.create_task(agent.run(run))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
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

    @app.post("/api/approvals/{identifier}")
    async def resolve_approval(identifier: str,body: ApprovalChoice):
        if not approvals.resolve(identifier,body.approved):
            raise HTTPException(409,"Approval is absent or already resolved")
        return {"id":identifier,"approved":body.approved}

    @app.get("/api/audit")
    async def get_audit():
        return audit.report()

    # The SPA never swallows missing API routes or nonexistent static assets.
    dist = Path(settings.frontend_dist)
    if (dist/"assets").is_dir():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/")
    async def index():
        if not (dist/"index.html").is_file():
            raise HTTPException(503,"Frontend not built. Run npm ci && npm run build in frontend/.")
        return FileResponse(dist/"index.html")

    return app


app = create_app()
