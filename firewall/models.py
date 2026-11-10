from decimal import Decimal
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Decision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool: str = Field(min_length=1, max_length=100)
    amount: Decimal
    currency: str = "USD"
    payee: str = Field(min_length=1, max_length=254)
    description: str = Field(default="", max_length=1000)

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


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    reason: str = Field(min_length=1)
    suspicious_excerpt: str = ""
    source: str = ""
