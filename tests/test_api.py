import time
from fastapi.testclient import TestClient
from airlock.app import create_app
from airlock.config import Settings
from airlock.paypal import MockPayPal
from firewall import RuleJudge


def poll(client,identifier,status="completed"):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        body=client.get(f"/api/runs/{identifier}").json()
        if body["status"]==status:
            return body
        time.sleep(.02)
    raise AssertionError(body)


def test_all_demo_scenarios_and_approval_single_consumption(tmp_path):
    settings=Settings(sqlite_path=str(tmp_path/"audit.sqlite3"),demo_mode=True,frontend_dist=str(tmp_path))
    with TestClient(create_app(settings,paypal=MockPayPal(),judge=RuleJudge())) as client:
        assert client.get("/api/health").status_code==200
        assert client.get("/api/config").json()["paypal_mode"]=="MOCK"
        assert client.get("/api/missing").status_code==404
        disabled=client.post("/api/runs",json={"message":"Buy me a USB-C charger under $40","firewall_enabled":False}).json()["id"]
        off=poll(client,disabled)
        assert len([e for e in off["events"] if e["type"]=="tool_result" and e["status"]=="succeeded"])==2
        enabled=client.post("/api/runs",json={"message":"Buy me a USB-C charger under $40","firewall_enabled":True}).json()["id"]
        on=poll(client,enabled)
        assert any(e.get("decision")=="BLOCK" and e.get("suspicious_excerpt") for e in on["events"])
        contractor=client.post("/api/runs",json={"message":"Also pay my contractor $120 invoice","firewall_enabled":True}).json()["id"]
        pending=poll(client,contractor,"awaiting_approval")
        identifier=pending["approvals"][0]["id"]
        assert client.post(f"/api/approvals/{identifier}",json={"approved":True}).status_code==200
        assert client.post(f"/api/approvals/{identifier}",json={"approved":True}).status_code==409
        final=poll(client,contractor)
        assert final["approvals"][0]["status"]=="approved"
        audit=client.get("/api/audit").json()
        assert len(audit["rows"])==5
        assert audit["stats"]=={"blocked_amount":500,"allowed_amount":678}
        assert all(row["mock"] for row in audit["rows"])
