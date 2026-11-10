import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import yaml
from firewall import AuditStore, Firewall, Policy, RuleJudge, build_judge
from .agent import AGENT_MODEL, DemoAgent
from .config import Settings
from .paypal import build_paypal
from .store import ApprovalManager, Run


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1,max_length=4000)
    firewall_enabled: bool = True


class ApprovalChoice(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    approved: bool


def create_app(settings=None, *, paypal=None, judge=None):
    settings = settings or Settings.from_env()
    policy = Policy.load(settings.policy_path)
    policy.allowed_payees = [settings.shop_email if x == "shop@chargers.example" else settings.contractor_email if x == "contractor@contractor.example" else x for x in policy.allowed_payees]
    audit = AuditStore(settings.sqlite_path)
    paypal = paypal or build_paypal(settings.paypal_client_id,settings.paypal_client_secret)
    judge = judge or build_judge(settings.anthropic_api_key)
    if isinstance(judge, RuleJudge):
        judge.shop_payee, judge.contractor_payee = settings.shop_email,settings.contractor_email
    if hasattr(judge,"trusted_contacts"):
        judge.trusted_contacts = {"shop":settings.shop_email,"my contractor":settings.contractor_email}
    firewall = Firewall(policy,judge,audit)
    approvals = ApprovalManager()
    agent = DemoAgent(settings,firewall,paypal,approvals)
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
    app.state.audit, app.state.runs, app.state.agent = audit,runs,agent

    @app.get("/api/health")
    async def health():
        return {"status":"ok"}

    @app.get("/api/config")
    async def config():
        return {"demo_mode":settings.scripted,"paypal_mode":paypal.mode,"agent_model":AGENT_MODEL,"judge_model":"claude-haiku-5-5" if judge.mode == "anthropic" else "rules (no API key)","policy_yaml":yaml.safe_dump(policy.model_dump(mode="json"),sort_keys=False),"judge_mode":judge.mode,"shop_email":settings.shop_email,"contractor_email":settings.contractor_email}

    @app.post("/api/runs",status_code=202)
    async def start(body: StartRun):
        if not body.message.strip():
            raise HTTPException(422,"Message cannot be blank")
        if any(run.status in {"running","awaiting_approval"} for run in runs.values()):
            raise HTTPException(409,"Finish the current demo run before starting another")
        run = Run(body.message,body.firewall_enabled)
        runs[run.id] = run
        task = asyncio.create_task(agent.run(run))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"id":run.id}

    @app.get("/api/runs/{identifier}")
    async def get_run(identifier: str):
        if identifier not in runs:
            raise HTTPException(404,"Run not found")
        return runs[identifier].snapshot()

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
