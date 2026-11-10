import asyncio
from dataclasses import dataclass, field
from uuid import uuid4
from firewall import Provenance


@dataclass
class Run:
    message: str
    firewall_enabled: bool
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "running"
    messages: list = field(default_factory=list)
    events: list = field(default_factory=list)
    approvals: list = field(default_factory=list)
    error: str | None = None

    def __post_init__(self):
        self.messages.append({"role":"user","content":self.message})
        self.provenance = Provenance(self.message)

    def snapshot(self):
        return {key:getattr(self,key) for key in ("id","status","firewall_enabled","messages","events","approvals","error")}

    async def emit(self, event):
        self.events.append({"id":uuid4().hex, **event})

    async def say(self, text):
        self.messages.append({"role":"assistant","content":text})


class ApprovalManager:
    def __init__(self):
        self.pending = {}

    async def request(self, run, identifier, call, verdict):
        future = asyncio.get_running_loop().create_future()
        card = {"id":identifier,"tool":call.tool,"amount":float(call.amount),"payee":call.payee,"reason":verdict.reason,"status":"pending"}
        self.pending[identifier] = (future, card)
        run.approvals.append(card)
        run.status = "awaiting_approval"
        try:
            approved = await future
            card["status"] = "approved" if approved else "denied"
            return approved
        finally:
            self.pending.pop(identifier, None)
            run.status = "running"

    def resolve(self, identifier, approved):
        item = self.pending.get(identifier)
        if not item or item[0].done():
            return False
        item[0].set_result(approved)
        return True
