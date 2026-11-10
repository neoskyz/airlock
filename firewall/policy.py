from decimal import Decimal
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import Decision, ToolCall, Verdict


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = "USD"
    single_transaction_limit: Decimal = Field(gt=0)
    daily_limit: Decimal = Field(gt=0)
    auto_approve_threshold: Decimal = Field(ge=0)
    allowed_payees: list[str]
    unknown_payee_action: Decision = Decision.REQUIRE_APPROVAL
    forbidden_tools: list[str] = []

    @model_validator(mode="after")
    def limits(self):
        if self.unknown_payee_action == Decision.ALLOW:
            raise ValueError("Unknown payees cannot be automatically allowed")
        if self.auto_approve_threshold > self.single_transaction_limit:
            raise ValueError("Automatic threshold cannot exceed single limit")
        return self

    @classmethod
    def load(cls, path: str | Path):
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def evaluate(self, call: ToolCall, daily_reserved: Decimal = Decimal(0)) -> Verdict:
        if call.tool in self.forbidden_tools:
            return Verdict(decision=Decision.BLOCK, reason="Tool forbidden by policy")
        if call.currency != self.currency:
            return Verdict(decision=Decision.BLOCK, reason="Currency is not permitted")
        if call.amount > self.single_transaction_limit:
            return Verdict(decision=Decision.BLOCK, reason="Single transaction limit exceeded")
        if daily_reserved + call.amount > self.daily_limit:
            return Verdict(decision=Decision.BLOCK, reason="Daily limit exceeded (including pending approvals)")
        if call.payee.lower() not in {payee.lower() for payee in self.allowed_payees}:
            return Verdict(decision=self.unknown_payee_action, reason="Payee is not on the allowlist")
        if call.amount > self.auto_approve_threshold:
            return Verdict(decision=Decision.REQUIRE_APPROVAL, reason=f"Amount exceeds automatic approval threshold (${self.auto_approve_threshold})")
        return Verdict(decision=Decision.ALLOW, reason="Deterministic policy passed")
