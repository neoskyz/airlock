import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    anthropic_api_key: str = ""
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    demo_mode: bool = False
    sqlite_path: str = "data/audit.sqlite3"
    policy_path: str = str(ROOT / "policies/default.yaml")
    shop_email: str = "shop@chargers.example"
    contractor_email: str = "contractor@contractor.example"
    frontend_dist: str = str(ROOT / "frontend/dist")

    @property
    def scripted(self):
        return self.demo_mode or not self.anthropic_api_key

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""), paypal_client_id=os.getenv("PAYPAL_CLIENT_ID", ""), paypal_client_secret=os.getenv("PAYPAL_CLIENT_SECRET", ""), demo_mode=os.getenv("DEMO_MODE", "false").lower() in {"true","1","yes"}, sqlite_path=os.getenv("SQLITE_PATH", "data/audit.sqlite3"), policy_path=os.getenv("POLICY_PATH", str(ROOT / "policies/default.yaml")), shop_email=os.getenv("PAYPAL_SHOP_EMAIL") or "shop@chargers.example", contractor_email=os.getenv("PAYPAL_CONTRACTOR_EMAIL") or "contractor@contractor.example")
