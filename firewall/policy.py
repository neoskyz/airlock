from decimal import Decimal
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .models import Decision, ToolCall, Verdict

POLICY_IDS = ("single_transaction", "period_budget", "allowlist", "categories", "risk_threshold")


class Policy(BaseModel):
    """Five configurable payment policies; all monetary arithmetic uses Decimal.

    ``daily_limit`` is the budget value for the selected ``period``. Its original
    name is retained for callers upgrading an existing Airlock installation.
    ``auto_approve_threshold`` is a risk feature, not a sixth policy.
    """
    model_config = ConfigDict(extra="forbid", validate_default=True)
    currency: str = "USD"
    single_transaction_limit: Decimal = Field(default=1000, gt=0)
    daily_limit: Decimal = Field(default=2000, gt=0)
    period: Literal["daily", "weekly"] = "weekly"
    auto_approve_threshold: Decimal = Field(default=100, ge=0)
    allowed_payees: list[str] = Field(default_factory=list)
    unknown_payee_action: Literal["REQUIRE_APPROVAL", "BLOCK"] = "REQUIRE_APPROVAL"
    blocked_categories: list[str] = Field(default_factory=lambda: ["gift cards", "crypto", "gambling"])
    risk_approval_threshold: int = Field(default=60, ge=1, le=99)
    risk_block_threshold: int = Field(default=85, ge=2, le=100)
    enabled: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(POLICY_IDS, True))

    @field_validator("enabled")
    @classmethod
    def valid_enabled(cls, value):
        if set(value) - set(POLICY_IDS):
            raise ValueError("Unknown policy id")
        return {**dict.fromkeys(POLICY_IDS, True), **value}

    @field_validator("single_transaction_limit", "daily_limit", "auto_approve_threshold")
    @classmethod
    def finite_money(cls, value):
        if not value.is_finite() or value != value.quantize(Decimal(".01")):
            raise ValueError("Policy amounts must be finite money with at most two decimals")
        return value

    @model_validator(mode="after")
    def limits(self):
        if self.risk_approval_threshold >= self.risk_block_threshold:
            raise ValueError("Approval threshold must be below block threshold")
        return self

    @classmethod
    def load(cls, path: str | Path):
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def evaluate(self, call: ToolCall, daily_reserved: Decimal = Decimal(0), *, risk_score: int = 0) -> Verdict:
        # Currency is an invariant of this ledger, not an editable sixth policy.
        if call.currency != self.currency:
            return Verdict(decision=Decision.BLOCK, reason="Currency is not supported by this ledger", policy="currency_invariant")
        if self.enabled["single_transaction"] and call.amount > self.single_transaction_limit:
            return Verdict(decision=Decision.BLOCK, reason="Single transaction limit exceeded", policy="single_transaction")
        if self.enabled["period_budget"] and daily_reserved + call.amount > self.daily_limit:
            return Verdict(decision=Decision.BLOCK, reason=f"{self.period.title()} period budget exceeded (including pending approvals)", policy="period_budget")
        if self.enabled["allowlist"] and self.unknown_payee_action == "BLOCK" and call.payee.lower() not in {p.lower() for p in self.allowed_payees}:
            return Verdict(decision=Decision.BLOCK, reason="Payee is not on the allowlist (hard-block mode)", policy="allowlist")
        category = call.category.lower().replace("_", " ").replace("-", " ").strip()
        blocked = {c.lower().replace("_", " ").replace("-", " ").strip() for c in self.blocked_categories}
        if self.enabled["categories"] and category in blocked:
            return Verdict(decision=Decision.BLOCK, reason=f"Restricted product category: {call.category}", policy="categories")
        if self.enabled["risk_threshold"]:
            if risk_score >= self.risk_block_threshold:
                return Verdict(decision=Decision.BLOCK, reason=f"Risk score {risk_score} meets block threshold {self.risk_block_threshold}", policy="risk_threshold")
            if risk_score >= self.risk_approval_threshold:
                return Verdict(decision=Decision.REQUIRE_APPROVAL, reason=f"Risk score {risk_score} meets approval threshold {self.risk_approval_threshold}", policy="risk_threshold")
        return Verdict(decision=Decision.ALLOW, reason="Payment policies passed", policy="risk_threshold")
