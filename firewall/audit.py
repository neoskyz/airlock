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
        for row in self.connection.execute("SELECT id FROM audit WHERE status='awaiting_approval'").fetchall():
            self.record_approval(row["id"], "cancelled", actor="System (server restart)")
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

    def insert(self, call: ToolCall, verdict: Verdict, run_id: str, enabled: bool, mock: bool, provenance: list, status: str, *, evidence=None):
        identifier = uuid4().hex
        metadata = verdict.model_dump(mode="json")
        metadata.update(category=call.category, description=call.description, product_id=call.product_id, computed_decision=verdict.decision.value, call=call.model_dump(mode="json"), approval_history=[], execution_result=None)
        metadata.update(evidence or {})
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

    def update_metadata(self, identifier, **values):
        stored = self.connection.execute("SELECT metadata FROM audit WHERE id=?", (identifier,)).fetchone()
        if stored is None:
            raise KeyError(identifier)
        metadata = json.loads(stored[0])
        metadata.update(values)
        self.connection.execute("UPDATE audit SET metadata=? WHERE id=?", (json.dumps(metadata, default=str), identifier))
        self.connection.commit()

    def record_approval(self, identifier, action, *, actor="System"):
        stored = self.connection.execute("SELECT metadata FROM audit WHERE id=?", (identifier,)).fetchone()
        if stored is None:
            raise KeyError(identifier)
        history = json.loads(stored[0]).get("approval_history", [])
        history.append({"actor":actor, "timestamp":datetime.now(timezone.utc).isoformat(), "action":action})
        self.update_metadata(identifier, approval_history=history)

    @staticmethod
    def _serialize(stored):
        row = dict(stored)
        metadata = json.loads(row.pop("metadata"))
        row.update({key:value for key,value in metadata.items() if key not in row})
        row["initial_reason"] = metadata.get("reason")
        defaults = {"risk_score":None, "factors":[], "latency":None,
            "ai_review":"not recorded", "policy":"", "mode":"Enforce" if row["firewall_enabled"] else "Monitor",
            "computed_decision":row["decision"], "category":"", "description":None, "product_id":"",
            "call":None, "policy_snapshot":None, "ai_review_detail":None, "approval_history":[], "execution_result":None}
        for key, default in defaults.items():
            row.setdefault(key, default)
        if "policy_checks" not in row:
            from .policy import POLICY_IDS
            row["policy_checks"] = [{"id":key,"enabled":None,"passed":None,"outcome":"unknown","reason":"Not recorded for this event"} for key in POLICY_IDS]
        row["enforced_decision"] = "ALLOW" if row["status"] in {"succeeded", "executing", "unknown"} else row["decision"] if row["firewall_enabled"] else "ALLOW"
        row["display_decision"] = decision_label(row["computed_decision"] if row["mode"] == "Monitor" else row["decision"], row["mode"])
        row["amount"] = row.pop("amount_cents") / 100
        row["mock"] = bool(row["mock"])
        row["firewall_enabled"] = bool(row["firewall_enabled"])
        row["provenance"] = json.loads(row["provenance"])
        return row

    def report(self):
        rows = [self._serialize(stored) for stored in self.connection.execute("SELECT * FROM audit ORDER BY timestamp DESC, id DESC")]
        blocked = sum((Decimal(str(row["amount"])) for row in rows if row["status"] in {"blocked", "denied"}), Decimal(0))
        allowed = sum((Decimal(str(row["amount"])) for row in rows if row["status"] == "succeeded"), Decimal(0))
        return {"rows":rows, "stats":{"blocked_amount":float(blocked), "allowed_amount":float(allowed)}}

    def event(self, identifier):
        stored = self.connection.execute("SELECT * FROM audit WHERE id=?", (identifier,)).fetchone()
        if stored is None:
            return None
        row = self._serialize(stored)
        keys = ("id", "timestamp", "tool", "amount", "currency", "decision", "computed_decision", "display_decision", "status")
        row["run_events"] = [{key:other[key] for key in keys} for other in
            (self._serialize(value) for value in self.connection.execute("SELECT * FROM audit WHERE run_id=? AND id<>? ORDER BY timestamp DESC, id DESC", (row["run_id"], identifier)))]
        return row

    def flagged(self, decision=None, policy=None):
        from .policy import POLICY_IDS
        rows = [row for row in self.report()["rows"] if row["tool"] in {"create_order", "create_payout"}
                and row["computed_decision"] in {"BLOCK", "REQUIRE_APPROVAL"}]
        if decision:
            rows = [row for row in rows if row["computed_decision"] == decision]
        if policy:
            rows = [row for row in rows if row["policy"] == policy or any(check["id"] == policy and check["outcome"] == "fail"
                for check in [*row["policy_checks"], *(check for recheck in row.get("policy_rechecks", []) for check in recheck["policy_checks"])])]
        totals = {}
        for row in rows:
            totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + Decimal(str(row["amount"]))
        return {"rows":rows, "stats":{"count":len(rows), "total_amount":f"{totals.get('USD', Decimal(0)):.2f}",
                "currency":"USD", "totals_by_currency":{currency:f"{amount:.2f}" for currency,amount in totals.items()}}, "policies":list(POLICY_IDS)}

    def reset_sandbox_data(self):
        """Erase only the local demo ledger; never change provider state."""
        with self.connection:
            self.connection.execute("DELETE FROM audit")

    def close(self):
        self.connection.close()
