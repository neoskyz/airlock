"""Explainable deterministic features. Tool text never establishes authorization."""
from decimal import Decimal
import re
from .models import RiskFactor


def prompt_amounts(text):
    return [Decimal(x) for x in re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)(?![\d.])", text)]


def score_call(call, provenance, policy, *, known_payee=False, recent_calls=0):
    user = provenance.user_message.lower()
    amounts = prompt_amounts(user)
    allowed = call.payee.lower() in {x.lower() for x in policy.allowed_payees}
    reasons = []

    def factor(name, points, reason):
        reasons.append(RiskFactor(name=name, points=points, reason=reason))

    payee_points = (0 if known_payee and call.tool != "create_payout" else 20) if allowed else 18
    if not policy.enabled["allowlist"]:
        payee_points = 0
    factor("Payee history", payee_points, "Unknown payee outside the allowlist" if not allowed else "First payment to an allowed payee" if not known_payee else "Allowlisted recipient still needs fresh transfer authorization" if call.tool == "create_payout" else "Previously used allowed payee")
    is_payout = call.tool == "create_payout"
    has_action = bool(re.match(r"\s*(?:(?:please|also)\s+)?(?:buy|pay)\b", user))
    negated = bool(re.search(r"\b(?:don't|do not|never|cancel)\b", user))
    insufficient = not has_action or negated or not amounts
    budget_points = 50 if negated else 30 if insufficient else 20 if call.amount > max(amounts) else 0
    factor("User amount / intent", budget_points, "User text does not establish a positive payment instruction and budget" if insufficient or negated else f"Amount exceeds the user's ${max(amounts):.2f} budget" if budget_points else "Amount is within the user's stated budget")
    factor("Automatic amount", 20 if call.amount > policy.auto_approve_threshold else 0, f"Amount {'exceeds' if call.amount > policy.auto_approve_threshold else 'is within'} the ${policy.auto_approve_threshold:.2f} automatic amount")
    factor("Payment type", 24 if is_payout else 0, "Payout transfers funds rather than creating a purchase order" if is_payout else "Purchase order creation")
    factor("Velocity", 10 if recent_calls >= 3 else 0, f"{recent_calls} payment calls in the last 60 seconds")
    tainted_source = None
    for source in provenance.sources[1:]:
        # An ordinary catalog purchase necessarily obtains merchant/price from a
        # tool. Only instruction-bearing fields relevant to this call taint it.
        if is_payout and call.payee.lower() in source.text.lower() and call.payee.lower() not in user:
            tainted_source = source
            break
        amount_in_source = bool(re.search(r"\$\s*" + re.escape(str(call.amount.normalize())) + r"(?:\.00)?\b", source.text))
        if call.amount > max(amounts, default=Decimal(0)) and amount_in_source and "checkout policy" in source.text.lower():
            tainted_source = source
            break
    factor("Provenance taint", 10 if tainted_source else 0, "Payment instruction exists only in untrusted tool output" if tainted_source else "No call-related tool-only payment instruction detected")
    factor("Category", 5 if not call.category and not is_payout else 0, "Product category unavailable" if not call.category and not is_payout else f"Catalog category: {call.category}" if call.category else "Not a catalog purchase")
    raw_score = sum(f.points for f in reasons)
    score = min(100, raw_score)
    if raw_score > 100:
        factor("Score cap", 100-raw_score, "Risk scores are capped at 100")
    excerpt, source_name = "", ""
    if tainted_source:
        match = re.search(r"Merchant checkout policy:.*?(?:before checkout\.|$)", tainted_source.text, re.DOTALL)
        excerpt = match.group(0) if match else tainted_source.text[:500]
        source_name = f"{tainted_source.id} · {tainted_source.name} (untrusted tool output)"
    return score, reasons, bool(tainted_source), excerpt, source_name
