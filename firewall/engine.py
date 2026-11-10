import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import json
import logging
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
        policy = self.policy.model_copy(deep=True)
        initial_reserved = self.audit.reserve_total(policy.period)
        mode = mode or ("Enforce" if enabled else "Monitor")
        if mode not in {"Monitor", "Enforce"}:
            raise ValueError("Mode must be Monitor or Enforce")
        enforce = mode == "Enforce"

        async def send(**event):
            if emit:
                try:
                    await emit(event)
                except Exception as exc:
                    # Observers cannot change payment execution or its audit
                    # outcome. Never include callback text or secrets in logs.
                    logging.getLogger(__name__).warning("Payment event observer failed: %s", type(exc).__name__)

        known_payee = self.audit.has_payee(call.payee)
        recent_calls = self.audit.recent_count()
        score, factors, tainted, excerpt, source = score_call(call, provenance, policy,
            known_payee=known_payee, recent_calls=recent_calls)
        preliminary = policy.evaluate(call, initial_reserved)
        rules_ms = (perf_counter()-started)*1000
        review_ms, ai_review, review_failed = 0.0, "skipped (low risk)", False
        judgment, cached, review_invoked = None, False, False
        if preliminary.decision == Decision.BLOCK:
            verdict = preliminary
            ai_review = "skipped (hard policy)"
        else:
            if 30 <= score <= 84 or tainted:
                review_invoked = True
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
                except (Exception, asyncio.TimeoutError) as exc:
                    logging.getLogger(__name__).warning("Intent review failed: %s: %s", type(exc).__name__, str(exc)[:300])
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

        def assess_current_policy(reserved, stage):
            # Keep the initial risk factors immutable. A policy edit changes
            # execution eligibility, with its own score and evidence snapshot.
            current = self.policy.model_copy(deep=True)
            current_score, current_factors, current_taint, _, _ = score_call(
                call, provenance, current, known_payee=known_payee, recent_calls=recent_calls)
            needs_review = (30 <= current_score <= 84 or current_taint) and not review_invoked
            if judgment and not review_failed:
                delta = judgment.score_adjustment
                if judgment.decision == Decision.BLOCK:
                    delta = max(delta, min(10, max(0, 92-current_score)))
                adjusted = max(0, min(100, current_score+delta))
                if adjusted != current_score:
                    current_factors.append(RiskFactor(name="Intent review", points=adjusted-current_score, reason=judgment.reason))
                current_score = adjusted
            final = current.evaluate(call, reserved, risk_score=current_score)
            if final.policy == "risk_threshold" and judgment and not review_failed and judgment.decision == Decision.BLOCK:
                final = Verdict(decision=Decision.BLOCK, reason=judgment.reason, policy="risk_threshold")
            # Hard constraints always win. Preserve the established review
            # failure path: an unavailable judge requires human approval even
            # when taint alone puts the computed score above the risk cutoff.
            if final.decision != Decision.BLOCK or (review_failed and final.policy == "risk_threshold" and stage != "after_approval"):
                if review_failed or needs_review:
                    reason = ("Intent review unavailable or invalid — fail-closed to human approval" if review_failed else
                              "Policy changed; intent review was not performed for the current risk — human approval required")
                    final = Verdict(decision=Decision.REQUIRE_APPROVAL, reason=reason, policy="risk_threshold")
                elif judgment and judgment.decision == Decision.REQUIRE_APPROVAL:
                    final = Verdict(decision=Decision.REQUIRE_APPROVAL, reason=judgment.reason, policy="risk_threshold")
            if judgment and not review_failed and final.decision == Decision.ALLOW:
                final.reason += "; " + judgment.reason
            evidence = {
                "stage":stage, "decision":final.decision.value, "policy_snapshot":current.model_dump(mode="json"),
                "risk_score":current_score, "factors":[factor.model_dump(mode="json") for factor in current_factors],
                "policy_checks":current.checks(call, reserved, risk_score=current_score,
                    review_required=review_failed or needs_review or bool(judgment and judgment.decision != Decision.ALLOW)),
            }
            if needs_review:
                evidence["ai_review"] = "not performed after policy change — human approval required"
            return final, evidence

        async with self._budget_lock:
            # Recheck after asynchronous review and reserve before yielding.
            reserved = self.audit.reserve_total(self.policy.period)
            current_check, current_evidence = assess_current_policy(reserved, "before_execution")
            if current_check.decision == Decision.BLOCK:
                verdict = apply_hard_block(current_check)
            else:
                verdict = verdict.model_copy(update={"decision":current_check.decision, "reason":current_check.reason, "policy":current_check.policy})
            status = "blocked" if enforce and verdict.decision == Decision.BLOCK else "awaiting_approval" if enforce and verdict.decision == Decision.REQUIRE_APPROVAL else "executing"
            reserved = self.audit.reserve_total(self.policy.period)
            reviewer = None if not review_invoked else "rules" if getattr(self.judge, "mode", None) == "rules" or ai_review.startswith("rules review") else "AI"
            evidence = {
                "policy_snapshot":policy.model_dump(mode="json"),
                "policy_checks":policy.checks(call, initial_reserved, risk_score=score,
                    review_required=review_failed or bool(judgment and judgment.decision != Decision.ALLOW)),
                "ai_review_detail":{"invoked":review_invoked, "model_invoked":review_invoked and reviewer == "AI" and not cached,
                    "reviewer":reviewer, "cached":cached, "status":"unavailable" if review_failed else "cached" if cached else "completed" if review_invoked else "skipped",
                    "decision":judgment.decision.value if judgment else None,
                    "reason":judgment.reason if judgment else "Intent review unavailable or invalid — fail-closed to human approval" if review_failed else ai_review,
                    "suspicious_excerpt":judgment.suspicious_excerpt if judgment else "", "source":judgment.source if judgment else "", "latency_ms":round(review_ms,2)},
            }
            if self.policy.model_dump(mode="json") != evidence["policy_snapshot"] or reserved != initial_reserved:
                evidence["policy_rechecks"] = [current_evidence]
            audit_id = self.audit.insert(call, verdict, run_id, enforce, mock, provenance.serialize(), status, evidence=evidence)
        outcome = {"computed_decision":verdict.decision.value,
                   "enforced_decision":verdict.decision.value if enforce else "ALLOW",
                   "display_decision":decision_label(verdict.decision, mode), "mode":mode}
        details = {"tool":call.tool,"amount":float(call.amount),"payee":call.payee,"mock":mock}
        try:
            await send(type="decision",id=audit_id,message=verdict.reason,**verdict.model_dump(mode="json"),status=status,
                computed_decision=outcome["computed_decision"],enforced_decision=outcome["enforced_decision"],display_decision=outcome["display_decision"],**details)
        except asyncio.CancelledError:
            # The provider has not been called. Release its reservation even
            # when cancellation arrives while publishing the first decision.
            if status != "blocked":
                self.audit.update(audit_id, status="cancelled", decision="BLOCK", reason="Payment cancelled before provider execution")
            self.audit.update_metadata(audit_id, latency={**verdict.latency.model_dump(), "end_to_end_ms":round((perf_counter()-started)*1000,2)})
            raise
        late_block = False
        if verdict.decision != Decision.BLOCK and self.policy.model_dump(mode="json") != current_evidence["policy_snapshot"]:
            async with self._budget_lock:
                reserved = max(self.audit.reserve_total(self.policy.period)-call.amount, 0)
                current_check, current_evidence = assess_current_policy(reserved, "before_provider")
                rechecks = self.audit.event(audit_id).get("policy_rechecks", [])
                self.audit.update_metadata(audit_id, policy_rechecks=[*rechecks, current_evidence])
                if current_check.decision != Decision.ALLOW:
                    verdict = verdict.model_copy(update={"decision":current_check.decision, "reason":current_check.reason, "policy":current_check.policy})
                    if enforce:
                        status = "blocked" if verdict.decision == Decision.BLOCK else "awaiting_approval"
                        late_block = status == "blocked"
                        self.audit.update(audit_id, status=status, decision=verdict.decision.value, reason=verdict.reason)
                        outcome.update(enforced_decision=verdict.decision.value, display_decision=decision_label(verdict.decision, mode))
        if enforce and verdict.decision == Decision.BLOCK:
            self.audit.update_metadata(audit_id, latency={**verdict.latency.model_dump(), "end_to_end_ms":round((perf_counter()-started)*1000,2)})
            if late_block:
                await send(type="tool_result",id=audit_id+":blocked",decision="BLOCK",status="blocked",message=verdict.reason,**outcome,**details)
            return {"decision":"BLOCK","status":"blocked","reason":verdict.reason,**outcome}
        if enforce and verdict.decision == Decision.REQUIRE_APPROVAL:
            approval_started = perf_counter()
            self.audit.record_approval(audit_id, "requested")
            try:
                approved = (await request_approval(audit_id,call,verdict)) is True if request_approval else False
            except asyncio.CancelledError:
                self.audit.update_metadata(audit_id, latency={**verdict.latency.model_dump(), "approval_ms":round((perf_counter()-approval_started)*1000,2), "end_to_end_ms":round((perf_counter()-started)*1000,2)})
                self.audit.record_approval(audit_id, "cancelled")
                self.audit.update(audit_id,status="cancelled",decision="BLOCK",reason="Approval cancelled")
                raise
            except Exception:
                approved = False
            history = self.audit.event(audit_id)["approval_history"]
            if not any(entry["action"] in {"approved", "denied"} for entry in history):
                self.audit.record_approval(audit_id, "approved" if approved else "denied", actor="Caller approval callback" if request_approval else "System (no approval callback)")
            latency = {**verdict.latency.model_dump(), "approval_ms":round((perf_counter()-approval_started)*1000,2)}
            self.audit.update_metadata(audit_id, latency=latency)
            if not approved:
                self.audit.update_metadata(audit_id, latency={**latency, "end_to_end_ms":round((perf_counter()-started)*1000,2)})
                self.audit.update(audit_id,status="denied",decision="BLOCK",reason="Human denied the payment")
                outcome.update(enforced_decision="BLOCK", display_decision=decision_label(Decision.BLOCK, mode))
                await send(type="tool_result",id=audit_id+":denied",decision="BLOCK",status="denied",message="Human denied the payment; no provider call made",**outcome,**details)
                return {"decision":"BLOCK","status":"denied",**outcome}
            async with self._budget_lock:
                reserved = max(self.audit.reserve_total(self.policy.period)-call.amount,0)
                final, final_evidence = assess_current_policy(reserved, "after_approval")
                rechecks = self.audit.event(audit_id).get("policy_rechecks", [])
                self.audit.update_metadata(audit_id, policy_rechecks=[*rechecks, final_evidence])
                if final.decision == Decision.BLOCK:
                    self.audit.update_metadata(audit_id, latency={**latency, "end_to_end_ms":round((perf_counter()-started)*1000,2)})
                    self.audit.update(audit_id,status="blocked",decision="BLOCK",reason=final.reason)
                    outcome.update(enforced_decision="BLOCK", display_decision=decision_label(Decision.BLOCK, mode))
                else:
                    self.audit.update(audit_id,status="executing",reason=verdict.reason+"; human approved")
            if final.decision == Decision.BLOCK:
                await send(type="tool_result",id=audit_id+":blocked",decision="BLOCK",status="blocked",message=final.reason,**outcome,**details)
                return {"decision":"BLOCK","status":"blocked",**outcome}
        outcome["enforced_decision"] = "ALLOW"
        execution_started = perf_counter()
        try:
            result = await execute(call,audit_id)
            identifier = result.get("id")
            if not identifier:
                raise ValueError("Payment provider returned no identifier")
            self.audit.update_metadata(audit_id, execution_result=result)
            self.audit.update(audit_id,status="succeeded",paypal_id=identifier,paypal_status=result.get("status"),reserved_day=datetime.now(timezone.utc).date().isoformat())
        except asyncio.CancelledError:
            self.audit.update(audit_id,status="unknown",reason="Execution interrupted; verify PayPal before retrying")
            raise
        except Exception:
            self.audit.update(audit_id,status="unknown",reason="PayPal call failed or outcome unknown; verify sandbox before retrying")
            await send(type="tool_result",id=audit_id+":error",decision="ALLOW",status="unknown",message="PayPal call failed or outcome unknown. Verify sandbox before retrying.",**outcome,**details)
            return {"decision":"ALLOW","status":"unknown","error":"PayPal call failed or outcome unknown",**outcome}
        else:
            await send(type="tool_result",id=audit_id+":result",decision="ALLOW",status="succeeded",message=f"Provider accepted {call.tool}: {result.get('status','CREATED')}",paypal_id=identifier,paypal_status=result.get("status"),**outcome,**details)
            return {**result,"decision":"ALLOW",**outcome}
        finally:
            latency = self.audit.event(audit_id)["latency"]
            self.audit.update_metadata(audit_id, latency={**latency, "execution_ms":round((perf_counter()-execution_started)*1000,2), "end_to_end_ms":round((perf_counter()-started)*1000,2)})
