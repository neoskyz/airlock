import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
AgentMode = Literal["live", "compromised"]


@dataclass
class Settings:
    anthropic_api_key: str = ""
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    demo_mode: bool = False
    agent_mode: AgentMode = "compromised"
    agent_model: str = "claude-sonnet-5-5"
    judge_model: str = "claude-haiku-5-5"
    sqlite_path: str = "data/audit.sqlite3"
    policy_path: str = str(ROOT / "policies/default.yaml")
    shop_email: str = "shop@chargers.example"
    contractor_email: str = "contractor@contractor.example"
    frontend_dist: str = str(ROOT / "frontend/dist")

    def __post_init__(self):
        if self.agent_mode not in {"live", "compromised"}:
            raise ValueError("AGENT_MODE must be live or compromised")

    def resolve_agent_mode(self, requested: AgentMode | None = None) -> AgentMode:
        if not self.anthropic_api_key:
            return "compromised"
        # DEMO_MODE is a legacy default; an explicit UI selection can override it.
        return requested or ("compromised" if self.demo_mode else self.agent_mode)

    @property
    def scripted(self):
        return self.resolve_agent_mode() == "compromised"

    @classmethod
    def from_env(cls):
        # The startup script may load .env. Application code reads process env only.
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            paypal_client_id=os.getenv("PAYPAL_CLIENT_ID", ""),
            paypal_client_secret=os.getenv("PAYPAL_CLIENT_SECRET", ""),
            demo_mode=os.getenv("DEMO_MODE", "false").lower() in {"true", "1", "yes"},
            agent_mode=os.getenv("AGENT_MODE", "compromised"),
            agent_model=os.getenv("AGENT_MODEL") or "claude-sonnet-5-5",
            judge_model=os.getenv("JUDGE_MODEL") or "claude-haiku-5-5",
            sqlite_path=os.getenv("SQLITE_PATH", "data/audit.sqlite3"),
            policy_path=os.getenv("POLICY_PATH", str(ROOT / "policies/default.yaml")),
            shop_email=os.getenv("PAYPAL_SHOP_EMAIL") or "shop@chargers.example",
            contractor_email=os.getenv("PAYPAL_CONTRACTOR_EMAIL") or "contractor@contractor.example",
        )
