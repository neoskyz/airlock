import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from .models import ToolCall, Verdict


class AuditStore:
    """Single-process SQLite ledger; cents avoid floating-point budget arithmetic."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS audit (
          id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, run_id TEXT NOT NULL,
          tool TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
          payee TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
          paypal_id TEXT, status TEXT NOT NULL, mock INTEGER NOT NULL,
          firewall_enabled INTEGER NOT NULL, suspicious_excerpt TEXT NOT NULL,
          source TEXT NOT NULL, provenance TEXT NOT NULL, paypal_status TEXT, reserved_day TEXT NOT NULL
        )""")
        # No automatic payment replay after restart. Unknown execution remains reserved.
        self.connection.execute("UPDATE audit SET decision='BLOCK', status='cancelled', reason=reason || '; server restarted before approval' WHERE status='awaiting_approval'")
        self.connection.execute("UPDATE audit SET status='unknown' WHERE status='executing'")
        self.connection.commit()

    def reserve_total(self) -> Decimal:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.connection.execute("SELECT COALESCE(SUM(amount_cents),0) FROM audit WHERE (reserved_day = ? AND status = 'succeeded') OR status IN ('awaiting_approval','executing','unknown')", (today,)).fetchone()
        return Decimal(row[0]) / 100

    def insert(self, call: ToolCall, verdict: Verdict, run_id: str, enabled: bool, mock: bool, provenance: list, status: str):
        identifier = uuid4().hex
        self.connection.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identifier, datetime.now(timezone.utc).isoformat(), run_id, call.tool, int(call.amount*100), call.currency,
            call.payee, verdict.decision.value, verdict.reason, None, status, int(mock), int(enabled),
            verdict.suspicious_excerpt, verdict.source, json.dumps(provenance), None, datetime.now(timezone.utc).date().isoformat()))
        self.connection.commit()
        return identifier

    def record_run_cancellation(self, run_id, enabled, mock):
        """Record cancellation even if no payment has yet been proposed."""
        now = datetime.now(timezone.utc)
        self.connection.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            uuid4().hex, now.isoformat(), run_id, "cancel_run", 0, "USD", "—",
            "BLOCK", "Run cancelled by user; submitted PayPal calls are not rolled back",
            None, "cancelled", int(mock), int(enabled), "", "user", "[]", None, now.date().isoformat()))
        self.connection.commit()

    def update(self, identifier, **values):
        if not values or not set(values) <= {"status","decision","reason","paypal_id","paypal_status","reserved_day"}:
            raise ValueError("Invalid audit update")
        self.connection.execute(f"UPDATE audit SET {', '.join(key+'=?' for key in values)} WHERE id=?", (*values.values(), identifier))
        self.connection.commit()

    def report(self):
        rows = []
        for stored in self.connection.execute("SELECT * FROM audit ORDER BY timestamp DESC"):
            row = dict(stored)
            row["amount"] = row.pop("amount_cents") / 100
            row["mock"] = bool(row["mock"])
            row["firewall_enabled"] = bool(row["firewall_enabled"])
            row["provenance"] = json.loads(row["provenance"])
            rows.append(row)
        blocked = sum((Decimal(str(row["amount"])) for row in rows if row["decision"] == "BLOCK"), Decimal(0))
        allowed = sum((Decimal(str(row["amount"])) for row in rows if row["status"] == "succeeded"), Decimal(0))
        return {"rows":rows, "stats":{"blocked_amount":float(blocked), "allowed_amount":float(allowed)}}

    def close(self):
        self.connection.close()
