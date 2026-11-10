from decimal import Decimal
import pytest
from pydantic import ValidationError
from firewall import Decision, Policy, ToolCall
from firewall.policy import POLICY_IDS


def policy(**kwargs):
    return Policy(**{"single_transaction_limit":1000,"daily_limit":2000,"auto_approve_threshold":100,"allowed_payees":["shop@chargers.example"],**kwargs})


def call(**kwargs):
    return ToolCall(**{"tool":"create_order","amount":"29","payee":"shop@chargers.example",**kwargs})


@pytest.mark.parametrize("amount,expected",[("1000",Decision.ALLOW),("1000.01",Decision.BLOCK)])
def test_single_transaction_boundaries(amount, expected):
    assert policy().evaluate(call(amount=amount)).decision == expected


def test_period_budget_counts_reservations_and_can_be_disabled():
    p = policy(period="weekly")
    assert p.evaluate(call(), Decimal("1972")).policy == "period_budget"
    assert p.evaluate(call(), Decimal("1971")).decision == Decision.ALLOW
    p.enabled["period_budget"] = False
    assert p.evaluate(call(), Decimal("2000")).decision == Decision.ALLOW


def test_allowlist_risk_and_optional_hard_block():
    assert policy().evaluate(call(payee="other@example.com")).decision == Decision.ALLOW
    p = policy(unknown_payee_action="BLOCK")
    assert p.evaluate(call(payee="other@example.com")).policy == "allowlist"
    p.enabled["allowlist"] = False
    assert p.evaluate(call(payee="other@example.com")).decision == Decision.ALLOW


@pytest.mark.parametrize("category", ["gift cards", "gift_cards", "crypto", "gambling"])
def test_restricted_catalog_categories(category):
    assert policy().evaluate(call(category=category)).policy == "categories"
    p = policy(); p.enabled["categories"] = False
    assert p.evaluate(call(category=category)).decision == Decision.ALLOW


@pytest.mark.parametrize("score,expected",[(59,Decision.ALLOW),(60,Decision.REQUIRE_APPROVAL),(84,Decision.REQUIRE_APPROVAL),(85,Decision.BLOCK),(100,Decision.BLOCK)])
def test_risk_thresholds(score, expected):
    assert policy().evaluate(call(), risk_score=score).decision == expected


def test_exactly_five_toggles_and_threshold_validation():
    assert set(policy().enabled) == set(POLICY_IDS)
    with pytest.raises(ValidationError):
        policy(enabled={"sixth":True})
    with pytest.raises(ValidationError):
        policy(risk_approval_threshold=90, risk_block_threshold=85)


def test_currency_is_a_ledger_invariant():
    assert policy().evaluate(call(currency="EUR")).decision == Decision.BLOCK


@pytest.mark.parametrize("amount",["0","-1","NaN","Infinity","1.001","1e999"])
def test_invalid_money_rejected(amount):
    with pytest.raises(ValidationError):
        call(amount=amount)


@pytest.mark.parametrize("field", ["daily_limit", "single_transaction_limit", "auto_approve_threshold"])
def test_policy_money_is_finite_and_decimal(field):
    with pytest.raises(ValidationError):
        policy(**{field:"1.001"})


def test_unknown_payee_cannot_be_auto_allowed():
    with pytest.raises(ValidationError):
        policy(unknown_payee_action="ALLOW")


def test_default_weekly_budget_has_room_for_the_demo():
    from pathlib import Path
    configured = Policy.load(Path(__file__).parents[1]/"policies/default.yaml")
    assert Policy().period == configured.period == "weekly"
    assert Policy().daily_limit == configured.daily_limit == Decimal("2000")
