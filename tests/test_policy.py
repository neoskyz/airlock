from decimal import Decimal
import pytest
from pydantic import ValidationError
from firewall import Decision, Policy, ToolCall


def policy():
    return Policy(single_transaction_limit=1000,daily_limit=2000,auto_approve_threshold=100,allowed_payees=["shop@chargers.example"])


def call(**kwargs):
    return ToolCall(**{"tool":"create_order","amount":"29","payee":"shop@chargers.example",**kwargs})


@pytest.mark.parametrize("amount,expected",[("100",Decision.ALLOW),("100.01",Decision.REQUIRE_APPROVAL),("1001",Decision.BLOCK)])
def test_amount_boundaries(amount,expected):
    assert policy().evaluate(call(amount=amount)).decision == expected


def test_daily_limit_counts_reservations():
    assert policy().evaluate(call(),Decimal("1972")).decision == Decision.BLOCK


def test_unknown_payee_needs_approval():
    assert policy().evaluate(call(payee="other@example.com")).decision == Decision.REQUIRE_APPROVAL


def test_forbidden_tool_and_currency():
    p=policy();p.forbidden_tools=["create_order"]
    assert p.evaluate(call()).decision == Decision.BLOCK
    assert policy().evaluate(call(currency="EUR")).decision == Decision.BLOCK


@pytest.mark.parametrize("amount",["0","-1","NaN","Infinity","1.001","1e999"])
def test_invalid_money_rejected(amount):
    with pytest.raises(ValidationError):
        call(amount=amount)


def test_unknown_payee_cannot_be_auto_allowed():
    with pytest.raises(ValidationError):
        Policy(single_transaction_limit=100,daily_limit=200,auto_approve_threshold=100,allowed_payees=[],unknown_payee_action="ALLOW")
