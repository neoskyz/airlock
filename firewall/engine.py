import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import json
from time import perf_counter
from .models import Decision, Latency, RiskFactor, Verdict, decision_label
from .judge import normalize_review_evidence
from .risk import score_call


class Firewall:
    """Reusable async execution boundary, independent of the agent and provider.

    Keep one instance per SQLite ledger/process to coordinate budget reservation.
    Monitor computes the same verdict and records it while allowing execution.
    """

    def __init__(self, policy, judge, audit, *, review_timeout=4.0):
        self.policy, self.judge, self.audit = policy, judge, audit
        self.review_timeout = review_timeout
        self._budget_lock = asyncio.Lock()
        self._reviews = OrderedDict()

    def wrap_tool(self, execute):
        """Wrap an async ``execute(ToolCall, request_id)`` provider adapter.

        The wrapped callable takes ``call, provenance`` plus the same keyword
        arguments as ``invoke``. Adapters retain ownership of Toolkit schemas,
        credentials and transport; Airlock owns the decision before execution.
        """
        async def protected(call, provenance, **kwargs):
            return await self.invoke(call, provenance, execute, **kwargs)
        return protected

    async def _review(self, call, provenance, run_id):
        key = (run_id, call.model_dump_json(), json.dumps(provenance.serialize(), sort_keys=True))
        if key in self._reviews:
            self._reviews.move_to_end(key)
            return self._reviews[key], True
        if getattr(self.judge, "mode", None) == "rules":
            verdict = Verdict.model_validate(await self.judge.judge(call, provenance))
        else:
            verdict = Verdict.model_validate(await asyncio.wait_for(self.judge.judge(call, provenance), timeout=self.review_timeout))
        excerpt, source = normalize_review_evidence(verdict.suspicious_excerpt, verdict.source, provenance)
        verdict = verdict.model_copy(update={"suspicious_excerpt":excerpt, "source":source})
        if verdict.ai_review.startswith("unavailable"):
            return verdict, False
        self._reviews[key] = verdict
        if len(self._reviews) > 256:
            self._reviews.popitem(last=False)
        return verdict, False

    async def invoke(self, call, provenance, execute, *, run_id="external", enabled=True,
                     mode=None, mock=False, request_approval=None, emit=None):
        started = perf_counter()
        mode = mode or ("Enforce" if enabled else "Monitor")
        if mode not in {"Monitor", "Enforce"}:
            raise ValueError("Mode must be Monitor or Enforce")
        enforce = mode == "Enforce"

        async def send(**event):
            if emit:
                await emit(event)

        score, factors, tainted, excerpt, source = score_call(call, provenance, self.policy,
            known_payee=self.audit.has_payee(call.payee), recent_calls=self.audit.recent_count())
        preliminary = self.policy.evaluate(call, self.audit.reserve_total(self.policy.period))
        rules_ms = (perf_counter()-started)*1000
        review_ms, ai_review, review_failed = 0.0, "skipped (low risk)", False
        judgment = None
        if preliminary.decision == Decision.BLOCK:
            verdict = preliminary
            ai_review = "skipped (hard policy)"
        else:
            if 30 <= score <= 84 or tainted:
                review_started = perf_counter()
                try:
                    judgment, cached = await self._review(call, provenance, run_id)
                    review_failed = judgment.ai_review.startswith("unavailable")
                    reviewer = "rules" if getattr(self.judge, "mode", None) == "rules" else "AI"
                    ai_review = judgment.ai_review if judgment.ai_review != "skipped (low risk)" else f"{reviewer} review: {'cached' if cached else 'completed'}"
                    delta = judgment.score_adjustment
                    # A verified unauthorized intent contributes at most 10
                    # points. Other changes must be explicitly returned by AI.
                    if judgment.decision == Decision.BLOCK:
                        delta = max(delta, min(10, max(0, 92-score)))
                    adjusted = max(0, min(100, score+delta))
                    if adjusted != score:
                        factors.append(RiskFactor(name="Intent review", points=adjusted-score, reason=judgment.reason))
                    score = adjusted
                    if judgment.suspicious_excerpt:
                        excerpt, source = judgment.suspicious_excerpt, judgment.source
                    elif not excerpt:
                        source = judgment.source or source
                except (Exception, asyncio.TimeoutError):
                    review_failed = True
                    ai_review = "unavailable — approval required (fail-closed)"
                review_ms = (perf_counter()-review_started)*1000
            elif score >= 85:
                ai_review = "skipped (high risk)"
            verdict = self.policy.evaluate(call, self.audit.reserve_total(self.policy.period), risk_score=score)
            if review_failed:
                verdict = Verdict(decision=Decision.REQUIRE_APPROVAL, reason="Intent review unavailable or invalid — fail-closed to human approval", policy="risk_threshold")
            elif judgment and judgment.decision == Decision.BLOCK:
                verdict = Verdict(decision=Decision.BLOCK, reason=judgment.reason, policy="risk_threshold")
            elif judgment and judgment.decision == Decision.REQUIRE_APPROVAL and verdict.decision == Decision.ALLOW:
                verdict = Verdict(decision=Decision.REQUIRE_APPROVAL, reason=judgment.reason, policy="risk_threshold")
            elif judgment:
                verdict.reason += "; " + judgment.reason
        verdict = verdict.model_copy(update={"risk_score":score,"factors":factors,"suspicious_excerpt":excerpt,"source":source,
            "mode":mode,"ai_review":ai_review,"latency":Latency(rules_ms=round(rules_ms,2),ai_review_ms=round(review_ms,2),total_ms=round((perf_counter()-started)*1000,2))})
        def apply_hard_block(hard):
            # Budget remains a hard constraint. For tool-origin attacks, retain
            # their security explanation first so a full ledger cannot hide it.
            reason = hard.reason
            if hard.policy == "period_budget" and tainted:
                security = ["Provenance taint: payment instruction exists only in untrusted tool output"]
                if any(f.name == "Payee history" and "Unknown payee" in f.reason for f in factors):
                    security.append("Unknown payee outside the allowlist")
                security.append("Payout not requested by user" if call.tool == "create_payout" else "Payment amount not requested by user")
                security.append(f"Risk score {score}")
                reason = "; ".join([*security, reason])
                if not any(f.name == "Period budget" for f in factors):
                    factors.append(RiskFactor(name="Period budget", points=0, reason=hard.reason))
            return verdict.model_copy(update={"decision":hard.decision, "reason":reason, "policy":hard.policy, "factors":factors})

        async with self._budget_lock:
            # Recheck after asynchronous review and reserve before yielding.
            budget_check = self.policy.evaluate(call, self.audit.reserve_total(self.policy.period))
            if budget_check.decision == Decision.BLOCK:
                verdict = apply_hard_block(budget_check)
            status = "blocked" if enforce and verdict.decision == Decision.BLOCK else "awaiting_approval" if enforce and verdict.decision == Decision.REQUIRE_APPROVAL else "executing"
            audit_id = self.audit.insert(call, verdict, run_id, enforce, mock, provenance.serialize(), status)
        outcome = {"computed_decision":verdict.decision.value,
                   "enforced_decision":verdict.decision.value if enforce else "ALLOW",
                   "display_decision":decision_label(verdict.decision, mode), "mode":mode}
        details = {"tool":call.tool,"amount":float(call.amount),"payee":call.payee,"mock":mock}
        await send(type="decision",id=audit_id,message=verdict.reason,**verdict.model_dump(mode="json"),status=status,
            computed_decision=outcome["computed_decision"],enforced_decision=outcome["enforced_decision"],display_decision=outcome["display_decision"],**details)
        if enforce and verdict.decision == Decision.BLOCK:
            return {"decision":"BLOCK","status":"blocked","reason":verdict.reason,**outcome}
        if enforce and verdict.decision == Decision.REQUIRE_APPROVAL:
            try:
                approved = bool(await request_approval(audit_id,call,verdict)) if request_approval else False
            except asyncio.CancelledError:
                self.audit.update(audit_id,status="cancelled",decision="BLOCK",reason="Approval cancelled")
                raise
            except Exception:
                approved = False
            if not approved:
                self.audit.update(audit_id,status="denied",decision="BLOCK",reason="Human denied the payment")
                outcome.update(enforced_decision="BLOCK", display_decision=decision_label(Decision.BLOCK, mode))
                await send(type="tool_result",id=audit_id+":denied",decision="BLOCK",status="denied",message="Human denied the payment; no provider call made",**outcome,**details)
                return {"decision":"BLOCK","status":"denied",**outcome}
            async with self._budget_lock:
                final = self.policy.evaluate(call,max(self.audit.reserve_total(self.policy.period)-call.amount,0),risk_score=verdict.risk_score)
                if final.decision == Decision.BLOCK:
                    self.audit.update(audit_id,status="blocked",decision="BLOCK",reason=final.reason)
                    outcome.update(enforced_decision="BLOCK", display_decision=decision_label(Decision.BLOCK, mode))
                    await send(type="tool_result",id=audit_id+":blocked",decision="BLOCK",status="blocked",message=final.reason,**outcome,**details)
                    return {"decision":"BLOCK","status":"blocked",**outcome}
                self.audit.update(audit_id,status="executing",reason=verdict.reason+"; human approved")
        outcome["enforced_decision"] = "ALLOW"
        try:
            result = await execute(call,audit_id)
            identifier = result.get("id")
            if not identifier:
                raise ValueError("Payment provider returned no identifier")
            self.audit.update(audit_id,status="succeeded",paypal_id=identifier,paypal_status=result.get("status"),reserved_day=datetime.now(timezone.utc).date().isoformat())
            await send(type="tool_result",id=audit_id+":result",decision="ALLOW",status="succeeded",message=f"Provider accepted {call.tool}: {result.get('status','CREATED')}",paypal_id=identifier,paypal_status=result.get("status"),**outcome,**details)
            return {**result,"decision":"ALLOW",**outcome}
        except asyncio.CancelledError:
            self.audit.update(audit_id,status="unknown",reason="Execution interrupted; verify PayPal before retrying")
            raise
        except Exception:
            self.audit.update(audit_id,status="unknown",reason="PayPal call failed or outcome unknown; verify sandbox before retrying")
            await send(type="tool_result",id=audit_id+":error",decision="ALLOW",status="unknown",message="PayPal call failed or outcome unknown. Verify sandbox before retrying.",**details)
            return {"decision":"ALLOW","status":"unknown","error":"PayPal call failed or outcome unknown",**outcome}
