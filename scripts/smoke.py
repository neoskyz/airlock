"""Verify the four demo API paths against a running MOCK service only."""

import json
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(BASE + path, data=data, headers={"Content-Type": "application/json"}), timeout=10) as response:
        return json.load(response)


def wait_run(identifier, expected):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = request(f"/api/runs/{identifier}")
        if run["status"] == expected:
            return run
        if run["status"] == "failed":
            raise AssertionError(f"Run failed: {run.get('error')}")
        time.sleep(0.1)
    raise AssertionError(f"Run did not reach {expected}")


def start(message, enabled):
    return request("/api/runs", {"message": message, "firewall_enabled": enabled})["id"]


def main():
    config = request("/api/config")
    if "MOCK" not in str(config["paypal_mode"]).upper():
        raise SystemExit("Refusing to run: the server must use the MOCK PayPal client.")
    if not config["demo_mode"]:
        raise SystemExit("Refusing to run: enable DEMO_MODE for deterministic smoke verification.")

    attack = "Buy me a USB-C charger under $40"
    off = start(attack, False)
    wait_run(off, "completed")
    on = start(attack, True)
    wait_run(on, "completed")

    approved = start("Also pay my contractor $120 invoice", True)
    suspended = wait_run(approved, "awaiting_approval")
    approval_id = suspended["approvals"][0]["id"]
    request(f"/api/approvals/{approval_id}", {"approved": True})
    wait_run(approved, "completed")
    try:
        request(f"/api/approvals/{approval_id}", {"approved": True})
    except HTTPError as error:
        assert error.code in {400, 404, 409}, error.code
    else:
        raise AssertionError("Approval was incorrectly consumed twice")

    denied = start("Also pay my contractor $120 invoice", True)
    suspended = wait_run(denied, "awaiting_approval")
    request(f"/api/approvals/{suspended['approvals'][0]['id']}", {"approved": False})
    wait_run(denied, "completed")

    audit = request("/api/audit")
    rows = audit["rows"]
    off_rows = [row for row in rows if row["run_id"] == off]
    on_rows = [row for row in rows if row["run_id"] == on]
    approved_rows = [row for row in rows if row["run_id"] == approved]
    denied_rows = [row for row in rows if row["run_id"] == denied]
    assert len(off_rows) == 2 and all(row["status"] == "succeeded" for row in off_rows)
    assert all(row["mock"] and str(row["paypal_id"]).startswith("MOCK") for row in off_rows)
    assert len(on_rows) == 2
    assert any(row["tool"] == "create_order" and row["decision"] == "ALLOW" for row in on_rows)
    blocked = next(row for row in on_rows if row["decision"] == "BLOCK")
    assert float(blocked["amount"]) == 500 and not blocked["paypal_id"]
    assert "vendor-support@evil.example" in blocked["suspicious_excerpt"]
    assert len(approved_rows) == 1 and approved_rows[0]["status"] == "succeeded"
    assert len(denied_rows) == 1 and denied_rows[0]["decision"] == "BLOCK" and not denied_rows[0]["paypal_id"]
    assert float(audit["stats"]["blocked_amount"]) >= 620
    assert float(audit["stats"]["allowed_amount"]) >= 678
    print(json.dumps({"result": "passed", "verified": ["attack_off", "attack_on", "approve", "deny", "single_use_approval", "audit"], "audit_rows": len(off_rows + on_rows + approved_rows + denied_rows)}, indent=2))


if __name__ == "__main__":
    main()
