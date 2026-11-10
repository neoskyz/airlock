import asyncio
from datetime import datetime, timezone
from .models import Decision, Verdict


class Firewall:
    """Wrap any async tool executor. No dependency on the demo agent or PayPal SDK.

    Callers own provenance, approval UI and tool transport. One instance per SQLite
    store/process coordinates reservation + daily budget. Deploy with one worker.
    """

    def __init__(self, policy, judge, audit):
        self.policy, self.judge, self.audit = policy, judge, audit
        self._budget_lock = asyncio.Lock()

    async def invoke(self, call, provenance, execute, *, run_id="external", enabled=True,
                     mock=False, request_approval=None, emit=None):
        async def send(**event):
            if emit:
                await emit(event)

        preliminary = self.policy.evaluate(call)
        if enabled:
            if preliminary.decision == Decision.BLOCK:
                verdict = preliminary
            else:
                try:
                    judgment = Verdict.model_validate(await self.judge.judge(call, provenance))
                except Exception:
                    judgment = Verdict(decision=Decision.BLOCK, reason="Intent judge error — fail-closed")
                if judgment.decision == Decision.BLOCK:
                    verdict = judgment
                elif preliminary.decision == Decision.REQUIRE_APPROVAL or judgment.decision == Decision.REQUIRE_APPROVAL:
                    verdict = Verdict(decision=Decision.REQUIRE_APPROVAL, reason=f"{preliminary.reason}; {judgment.reason}", source=judgment.source)
                else:
                    verdict = Verdict(decision=Decision.ALLOW, reason=f"{preliminary.reason}; {judgment.reason}", source=judgment.source)
        else:
            verdict = Verdict(decision=Decision.ALLOW, reason="Firewall OFF — policy and intent checks bypassed for attack demonstration")
        async with self._budget_lock:
            if enabled and verdict.decision != Decision.BLOCK:
                budget_check = self.policy.evaluate(call, self.audit.reserve_total())
                if budget_check.decision == Decision.BLOCK:
                    verdict = budget_check
            status = "blocked" if verdict.decision == Decision.BLOCK else "awaiting_approval" if verdict.decision == Decision.REQUIRE_APPROVAL else "executing"
            audit_id = self.audit.insert(call, verdict, run_id, enabled, mock, provenance.serialize(), status)
        details = {"tool":call.tool, "amount":float(call.amount), "payee":call.payee, "mock":mock}
        await send(type="decision", id=audit_id, message=verdict.reason, **verdict.model_dump(mode="json"), status=status, **details)
        if verdict.decision == Decision.BLOCK:
            return {"decision":"BLOCK", "status":"blocked", "reason":verdict.reason}
        if verdict.decision == Decision.REQUIRE_APPROVAL:
            try:
                approved = bool(await request_approval(audit_id, call, verdict)) if request_approval else False
            except asyncio.CancelledError:
                self.audit.update(audit_id, status="cancelled", decision="BLOCK", reason="Approval cancelled")
                raise
            except Exception:
                approved = False
            if not approved:
                self.audit.update(audit_id, status="denied", decision="BLOCK", reason="Human denied the payment")
                await send(type="tool_result", id=audit_id+":denied", decision="BLOCK", status="denied", message="Human denied the payment; no PayPal call made", **details)
                return {"decision":"BLOCK", "status":"denied"}
            # Recheck day rollover/budget after a delayed approval. Existing reservation
            # counts this call, so remove its amount before reevaluating.
            async with self._budget_lock:
                final = self.policy.evaluate(call, max(self.audit.reserve_total()-call.amount, 0))
                if final.decision == Decision.BLOCK:
                    self.audit.update(audit_id, status="blocked", decision="BLOCK", reason=final.reason)
                    await send(type="tool_result", id=audit_id+":blocked", decision="BLOCK", status="blocked", message=final.reason, **details)
                    return {"decision":"BLOCK", "status":"blocked"}
                self.audit.update(audit_id, status="executing", decision="ALLOW", reason=verdict.reason+"; human approved")
        try:
            result = await execute(call, audit_id)
            identifier = result.get("id")
            if not identifier:
                raise ValueError("Payment provider returned no identifier")
            self.audit.update(audit_id, status="succeeded", paypal_id=identifier, paypal_status=result.get("status"), reserved_day=datetime.now(timezone.utc).date().isoformat())
            await send(type="tool_result", id=audit_id+":result", decision="ALLOW", status="succeeded", message=f"PayPal accepted {call.tool}: {result.get('status', 'CREATED')}", paypal_id=identifier, paypal_status=result.get("status"), **details)
            return {"decision":"ALLOW", **result}
        except asyncio.CancelledError:
            self.audit.update(audit_id, status="unknown", reason="Execution interrupted; verify PayPal before retrying")
            raise
        except Exception:
            # A transport failure can follow acceptance. Keep its amount reserved.
            self.audit.update(audit_id, status="unknown", reason="PayPal call failed or outcome unknown; verify sandbox before retrying")
            await send(type="tool_result", id=audit_id+":error", decision="ALLOW", status="unknown", message="PayPal call failed or outcome unknown. Verify sandbox before retrying.", **details)
            return {"decision":"ALLOW", "status":"unknown", "error":"PayPal call failed or outcome unknown"}
