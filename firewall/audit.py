import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from .models import ToolCall, Verdict, decision_label


class AuditStore:
    """Single-process SQLite ledger with integer cents and additive migrations."""

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
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(audit)")}
        if "metadata" not in columns:
            self.connection.execute("ALTER TABLE audit ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        self.connection.execute("UPDATE audit SET decision='BLOCK', status='cancelled', reason=reason || '; server restarted before approval' WHERE status='awaiting_approval'")
        self.connection.execute("UPDATE audit SET status='unknown' WHERE status='executing'")
        self.connection.commit()

    def reserve_total(self, period="daily") -> Decimal:
        now = datetime.now(timezone.utc)
        start = now.date() - timedelta(days=now.weekday() if period == "weekly" else 0)
        row = self.connection.execute("SELECT COALESCE(SUM(amount_cents),0) FROM audit WHERE (reserved_day >= ? AND status = 'succeeded') OR status IN ('awaiting_approval','executing','unknown')", (start.isoformat(),)).fetchone()
        return Decimal(row[0]) / 100

    def has_payee(self, payee):
        return self.connection.execute("SELECT 1 FROM audit WHERE lower(payee)=lower(?) AND status='succeeded' LIMIT 1", (payee,)).fetchone() is not None

    def recent_count(self, seconds=60):
        cutoff = (datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat()
        return self.connection.execute("SELECT COUNT(*) FROM audit WHERE timestamp>=? AND amount_cents>0", (cutoff,)).fetchone()[0]

    def insert(self, call: ToolCall, verdict: Verdict, run_id: str, enabled: bool, mock: bool, provenance: list, status: str):
        identifier = uuid4().hex
        metadata = verdict.model_dump(mode="json")
        metadata.update(category=call.category, product_id=call.product_id, computed_decision=verdict.decision.value)
        self.connection.execute("INSERT INTO audit (id,timestamp,run_id,tool,amount_cents,currency,payee,decision,reason,paypal_id,status,mock,firewall_enabled,suspicious_excerpt,source,provenance,paypal_status,reserved_day,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identifier, datetime.now(timezone.utc).isoformat(), run_id, call.tool, int(call.amount*100), call.currency,
            call.payee, verdict.decision.value, verdict.reason, None, status, int(mock), int(enabled),
            verdict.suspicious_excerpt, verdict.source, json.dumps(provenance), None, datetime.now(timezone.utc).date().isoformat(), json.dumps(metadata)))
        self.connection.commit()
        return identifier

    def record_run_cancellation(self, run_id, enabled, mock):
        self._record_action("cancel_run", "Run cancelled by user; submitted PayPal calls are not rolled back", {"mode":"Enforce" if enabled else "Monitor"}, run_id=run_id, status="cancelled", enabled=enabled, mock=mock, decision="BLOCK")

    def record_config_change(self, kind, before, after):
        return self._record_action(f"{kind}_updated", f"{kind.title()} configuration updated", {"before":before, "after":after}, status="recorded")

    def _record_action(self, tool, reason, metadata, *, run_id="configuration", status="recorded", enabled=True, mock=False, decision="ALLOW"):
        identifier = uuid4().hex
        now = datetime.now(timezone.utc)
        self.connection.execute("INSERT INTO audit (id,timestamp,run_id,tool,amount_cents,currency,payee,decision,reason,paypal_id,status,mock,firewall_enabled,suspicious_excerpt,source,provenance,paypal_status,reserved_day,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identifier, now.isoformat(), run_id, tool, 0, "USD", "—", decision, reason,
            None, status, int(mock), int(enabled), "", "console", "[]", None, now.date().isoformat(), json.dumps(metadata, default=str)))
        self.connection.commit()
        return identifier

    def update(self, identifier, **values):
        if not values or not set(values) <= {"status","decision","reason","paypal_id","paypal_status","reserved_day"}:
            raise ValueError("Invalid audit update")
        self.connection.execute(f"UPDATE audit SET {', '.join(key+'=?' for key in values)} WHERE id=?", (*values.values(), identifier))
        self.connection.commit()

    def report(self):
        rows = []
        for stored in self.connection.execute("SELECT * FROM audit ORDER BY timestamp DESC"):
            row = dict(stored)
            metadata = json.loads(row.pop("metadata"))
            row.update({key:metadata.get(key, default) for key, default in {"risk_score":0,"factors":[],"latency":{"rules_ms":0,"ai_review_ms":0,"total_ms":0},"ai_review":"not recorded","policy":"","mode":"Enforce" if row["firewall_enabled"] else "Monitor","computed_decision":row["decision"],"category":"","product_id":""}.items()})
            for key in ("before", "after"):
                if key in metadata:
                    row[key] = metadata[key]
            row["enforced_decision"] = "ALLOW" if row["status"] in {"succeeded", "executing", "unknown"} else row["decision"] if row["firewall_enabled"] else "ALLOW"
            row["display_decision"] = decision_label(row["computed_decision"] if row["mode"] == "Monitor" else row["decision"], row["mode"])
            row["amount"] = row.pop("amount_cents") / 100
            row["mock"] = bool(row["mock"])
            row["firewall_enabled"] = bool(row["firewall_enabled"])
            row["provenance"] = json.loads(row["provenance"])
            rows.append(row)
        blocked = sum((Decimal(str(row["amount"])) for row in rows if row["status"] in {"blocked", "denied"}), Decimal(0))
        allowed = sum((Decimal(str(row["amount"])) for row in rows if row["status"] == "succeeded"), Decimal(0))
        return {"rows":rows, "stats":{"blocked_amount":float(blocked), "allowed_amount":float(allowed)}}

    def reset_sandbox_data(self):
        """Erase only the local demo ledger; never change provider state."""
        with self.connection:
            self.connection.execute("DELETE FROM audit")

    def close(self):
        self.connection.close()
