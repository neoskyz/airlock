"""Explainable deterministic features. Tool text never establishes authorization."""
from decimal import Decimal
import re
from .models import RiskFactor


def prompt_amounts(text):
    return [Decimal(x) for x in re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)(?![\d.])", text)]


def score_call(call, provenance, policy, *, known_payee=False, recent_calls=0):
    user = provenance.user_message.lower()
    amounts = prompt_amounts(user)
    allowed = call.risk_recipient.lower() in {x.lower() for x in policy.allowed_payees}
    reasons, weights = [], policy.risk_weights

    def factor(code, name, points, reason, parameter="", policy_id="risk_threshold"):
        reasons.append(RiskFactor(code=code, name=name, points=points, reason=reason,
                                  parameter=parameter, policy_id=policy_id))

    is_payout = call.tool == "create_payout"
    has_action = bool(re.match(r"\s*(?:(?:please|also)\s+)?(?:buy|pay)\b", user))
    negated = bool(re.search(r"\b(?:don't|do not|never|cancel)\b", user))
    insufficient = not has_action or negated or not amounts
    # A small unknown merchant receives lower exposure weight only when an
    # affirmative, budgeted purchase matches the requested product. A low
    # amount alone never establishes authorization.
    requested_products = {word for word in re.findall(r"[a-z]+", user)
                          if word not in {"buy", "me", "the", "cheapest", "a", "an", "under", "please", "for", "my", "with", "and", "usb", "c", "w", "noise", "cancelling"}}
    description_words = set(re.findall(r"[a-z]+", call.description.lower()))
    product_matches = bool(requested_products & description_words)
    low_exposure_purchase = (call.tool == "create_order" and not insufficient and
        "buy" in user and bool(call.category) and product_matches and
        call.amount <= max(amounts) and call.amount <= policy.auto_approve_threshold)
    payee_weight = "new_payee" if allowed and (not known_payee or is_payout) else "unknown_merchant" if not allowed and call.tool == "create_order" and call.merchant and not low_exposure_purchase else "unknown_payee"
    payee_points = getattr(weights, payee_weight) if not allowed or not known_payee or is_payout else 0
    if not policy.enabled["allowlist"]:
        payee_points = 0
    factor("new_payee" if allowed else "unknown_payee", "Unknown payee" if not allowed else "New payee", payee_points,
           "Unknown payee outside the allowlist; low exposure purchase matches the user's request" if not allowed and low_exposure_purchase else
           "Unknown payee outside the allowlist" if not allowed else "First payment to an allowed payee" if not known_payee else
           "Allowlisted recipient still needs fresh transfer authorization" if is_payout else "Previously used allowed payee",
           "risk_weights." + payee_weight, "allowlist")
    intent_weight = "negated" if negated else "not_requested" if insufficient else "over_budget"
    budget_points = getattr(weights, intent_weight) if insufficient or negated or call.amount > max(amounts) else 0
    factor("not_requested", "Not requested by user", budget_points,
           "User text does not establish a positive payment instruction and budget" if insufficient or negated else
           f"Amount exceeds the user's ${max(amounts):.2f} budget" if budget_points else "Amount is within the user's stated budget",
           "risk_weights." + intent_weight)
    above_limit = call.amount > policy.auto_approve_threshold
    factor("above_auto_approve_limit", "Above auto-approve limit", weights.above_auto_approve_limit if above_limit else 0,
           f"${call.amount:.2f} {'exceeds' if above_limit else 'is within'} the ${policy.auto_approve_threshold:.2f} auto-approve limit",
           "auto_approve_threshold")
    factor("payout_tool", "Payout tool", weights.payout_tool if is_payout else 0,
           "Payout transfers funds rather than creating a purchase order" if is_payout else "Purchase order creation", "risk_weights.payout_tool")
    velocity_weight = "unknown_merchant_velocity" if call.tool == "create_order" and call.merchant and not allowed else "velocity"
    factor("velocity", "Velocity", getattr(weights, velocity_weight) if recent_calls >= policy.velocity_call_threshold else 0,
           f"{recent_calls} payment calls in the last {policy.velocity_window_seconds} seconds; threshold {policy.velocity_call_threshold} calls",
           "velocity_call_threshold")
    tainted_source = None
    for source in provenance.sources[1:]:
        if is_payout and call.payee.lower() in source.text.lower() and call.payee.lower() not in user:
            tainted_source = source
            break
        amount_in_source = bool(re.search(r"\$\s*" + re.escape(str(call.amount.normalize())) + r"(?:\.00)?\b", source.text))
        if call.amount > max(amounts, default=Decimal(0)) and amount_in_source and "checkout policy" in source.text.lower():
            tainted_source = source
            break
    factor("untrusted_source", "Untrusted source", weights.untrusted_source if tainted_source else 0,
           "Payment instruction exists only in untrusted tool output" if tainted_source else "No call-related tool-only payment instruction detected", "risk_weights.untrusted_source")
    factor("category", "Category", weights.category if not call.category and not is_payout else 0,
           "Product category unavailable" if not call.category and not is_payout else f"Catalog category: {call.category}" if call.category else "Not a catalog purchase", "risk_weights.category", "categories")
    raw_score = sum(f.points for f in reasons)
    score = min(100, raw_score)
    if raw_score > 100:
        factor("score_cap", "Score cap", 100-raw_score, "Risk scores are capped at 100")
    excerpt, source_name = "", ""
    if tainted_source:
        match = re.search(r"Merchant checkout policy:.*?(?:before checkout\.|$)", tainted_source.text, re.DOTALL)
        excerpt = match.group(0) if match else tainted_source.text[:500]
        source_name = f"{tainted_source.id} · {tainted_source.name} (untrusted tool output)"
    return score, reasons, bool(tainted_source), excerpt, source_name
