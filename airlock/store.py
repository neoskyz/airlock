import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4
from firewall import Provenance


@dataclass
class Run:
    message: str
    firewall_enabled: bool
    agent_mode: str = "live"
    agent_provider: str = "scripted"
    agent_model: str = "Scripted agent"
    judge_model: str = "rules (no API key)"
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "running"
    messages: list = field(default_factory=list)
    events: list = field(default_factory=list)
    approvals: list = field(default_factory=list)
    error: str | None = None
    judge_mode: str = "rules"
    llm_degraded: bool = False
    products: dict = field(default_factory=dict)
    selected_product: dict | None = None
    revision: int = 0

    def __post_init__(self):
        self.messages.append({"role":"user","content":self.message})
        self.provenance = Provenance(self.message)

    def snapshot(self):
        return deepcopy({key:getattr(self,key) for key in ("revision","id","status","firewall_enabled","agent_mode","agent_provider","agent_model","judge_model","messages","events","approvals","error","judge_mode","llm_degraded")})

    def client_snapshot(self):
        """Agent-facing projection: security review evidence belongs in the console."""
        events = []
        for event in self.events:
            kind = event.get("type")
            if kind == "decision":
                if event.get("status") == "awaiting_approval":
                    events.append({"id":event["id"], "type":"client_status", "status":"awaiting_approval", "message":"Awaiting payment approval"})
                elif event.get("status") == "blocked":
                    events.append({"id":event["id"], "type":"tool_result", "status":"blocked", "decision":"BLOCK", "message":"Payment tool rejected the request", "tool":event.get("tool"), "amount":event.get("amount"), "payee":event.get("payee")})
                continue
            if kind not in {"searching", "products", "product_page", "checkout", "tool_result", "cancelled", "catalog_error", "run_status"}:
                continue
            safe = {key:value for key,value in event.items() if key in {"id","type","status","decision","message","products","product","attribution","tool","amount","payee","paypal_id","paypal_status","mock"}}
            if kind == "product_page":
                safe["product"] = {key:value for key,value in safe["product"].items() if key != "page_text"}
            if kind == "tool_result" and event.get("status") in {"blocked", "denied"}:
                safe["message"] = "Payment tool rejected the request"
            events.append(safe)
        approvals = [{"id":card["id"], "run_id":self.id, "status":card["status"],
                      **{key:card[key] for key in ("amount", "payee", "tool") if key in card},
                      "console_url":f"/console?approval={card['id']}#approvals"}
                     for card in self.approvals]
        return deepcopy({"revision":self.revision, "id":self.id, "status":self.status, "messages":self.messages,
                         "events":events, "approvals":approvals, "error":self.error,
                         "agent_mode":self.agent_mode, "llm_degraded":self.llm_degraded})

    def ensure_active(self):
        if self.status not in {"running", "awaiting_approval"}:
            raise asyncio.CancelledError("Run is no longer active")

    def set_status(self, status):
        # A payment result or agent summary cannot resurrect a stopped task.
        if self.status in {"completed", "failed", "cancelled"} or self.status == status:
            return
        if self.status == "cancelling" and status != "cancelled":
            return
        self.status = status
        self._append_event({"type":"run_status", "status":status,
                            "message":status.replace("_", " ")})

    def _append_event(self, event):
        from datetime import datetime, timezone
        self.events.append({"id":uuid4().hex, "timestamp":datetime.now(timezone.utc).isoformat(), **event})
        self.revision += 1

    async def emit(self, event):
        self._append_event(event)

    async def say(self, text):
        self.messages.append({"role":"assistant","content":text})
        self.revision += 1


class ApprovalManager:
    def __init__(self):
        self.pending = {}

    async def request(self, run, identifier, call, verdict):
        run.ensure_active()
        future = asyncio.get_running_loop().create_future()
        context = verdict.model_dump(mode="json")
        card = {**context,"id":identifier,"run_id":run.id,"user_request":run.message,"tool":call.tool,"amount":float(call.amount),"payee":call.payee,"status":"pending", "provenance":run.provenance.serialize()}
        self.pending[identifier] = (future, card)
        run.approvals.append(card)
        run.set_status("awaiting_approval")
        try:
            approved = await future
            card["status"] = "approved" if approved else "denied"
            return approved
        except asyncio.CancelledError:
            card["status"] = "cancelled"
            raise
        finally:
            self.pending.pop(identifier, None)
            if run.status == "awaiting_approval":
                run.set_status("running")

    def cancel_run(self, run):
        for card in run.approvals:
            item = self.pending.pop(card["id"], None)
            if item:
                card["status"] = "cancelled"
                item[0].cancel()

    def can_resolve(self, identifier):
        item = self.pending.get(identifier)
        return item is not None and not item[0].done()

    def resolve(self, identifier, approved):
        if not self.can_resolve(identifier):
            return False
        self.pending[identifier][0].set_result(approved)
        return True
