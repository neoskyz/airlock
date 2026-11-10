from decimal import Decimal
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Decision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


def decision_label(decision, mode):
    if mode == "Monitor":
        if decision == Decision.BLOCK:
            return "Would have blocked"
        if decision == Decision.REQUIRE_APPROVAL:
            return "Would require approval"
    return {Decision.ALLOW:"Allowed", Decision.BLOCK:"Blocked", Decision.REQUIRE_APPROVAL:"Approval required"}.get(decision, str(decision))


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool: str = Field(min_length=1, max_length=100)
    amount: Decimal
    currency: str = "USD"
    payee: str = Field(min_length=1, max_length=254)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=100)
    product_id: str = Field(default="", max_length=200)
    merchant: str = Field(default="", max_length=254)

    @property
    def risk_recipient(self):
        return self.merchant if self.tool == "create_order" and self.merchant else self.payee

    @field_validator("amount")
    @classmethod
    def valid_money(cls, value):
        if not value.is_finite() or value <= 0 or value > Decimal("1000000000000"):
            raise ValueError("Amount is outside supported money range")
        if value != value.quantize(Decimal(".01")):
            raise ValueError("Amount must be positive finite money with at most two decimals")
        return value


class Source(BaseModel):
    id: str
    origin: Literal["user", "tool"]
    name: str
    text: str


class RiskFactor(BaseModel):
    name: str
    points: int
    reason: str


class Latency(BaseModel):
    rules_ms: float = 0
    ai_review_ms: float = 0
    total_ms: float = 0
    decision_ms: float | None = None


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    reason: str = Field(min_length=1)
    suspicious_excerpt: str = ""
    source: str = ""
    risk_score: int = Field(default=0, ge=0, le=100)
    factors: list[RiskFactor] = Field(default_factory=list)
    latency: Latency = Field(default_factory=Latency)
    ai_review: str = "skipped (low risk)"
    policy: str = "risk_threshold"
    mode: Literal["Monitor", "Enforce"] = "Enforce"
    score_adjustment: int = Field(default=0, ge=-10, le=10)
