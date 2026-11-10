import type { AuditRow, RiskFactor } from './types';

export function decisionTitle(row: AuditRow): string {
  const decision = row.computed_decision || row.decision;
  const prefix = row.mode === 'Monitor' && decision === 'BLOCK' ? 'Would have blocked' : row.mode === 'Monitor' && decision === 'REQUIRE_APPROVAL' ? 'Would require approval' : decision === 'BLOCK' ? 'Blocked' : decision === 'REQUIRE_APPROVAL' ? 'Approval required' : 'Allowed';
  const factor = (row.factors || []).filter(item => item.points > 0).reduce<RiskFactor | undefined>((top, item) => !top || item.points > top.points ? item : top, undefined);
  const templates: Record<string, string> = {
    untrusted_source: 'payment instruction came from an untrusted product page',
    new_payee: 'first payment to an allowed recipient',
    unknown_payee: decision === 'ALLOW' ? 'requested purchase is within budget with a new merchant' : (row.factors || []).some(item => item.code === 'above_auto_approve_limit' && item.points > 0) ? 'above auto-approve limit for a new merchant' : 'recipient has no established payment history',
    not_requested: 'payment was not authorized by the user request',
    above_auto_approve_limit: 'amount exceeds the auto-approve limit',
    payout_tool: 'payout transfers funds outside purchase checkout',
    category: 'restricted product category',
    velocity: 'recent payment activity needs review',
    single_transaction: 'amount exceeds the single-transaction limit',
    period_budget: 'payment exceeds the period budget',
    intent_review: 'intent review requires human approval',
  };
  const hardPolicies: Record<string, string> = { single_transaction: 'amount exceeds the single-transaction limit', period_budget: 'payment exceeds the period budget', categories: 'restricted product category', allowlist: 'recipient is outside the required allowlist', currency_invariant: 'currency is unsupported by the payment ledger' };
  if (decision === 'BLOCK' && row.policy && hardPolicies[row.policy]) return `${prefix}: ${hardPolicies[row.policy]}`;
  const reason = factor?.code && templates[factor.code] || (decision === 'ALLOW' ? 'payment matches the user request and payment policies' : 'payment requires policy intervention');
  return `${prefix}: ${row.tool === 'create_payout' ? reason.replace('requested purchase', 'requested payment').replace('purchase matches', 'payment matches') : reason}`;
}

export const reasonNames: Record<string, string> = {
  untrusted_source: 'Untrusted source', unknown_payee: 'Unknown payee', new_payee: 'New payee', not_requested: 'Not requested by user',
  above_auto_approve_limit: 'Above auto-approve limit', payout_tool: 'Payout tool', category: 'Category', velocity: 'Velocity',
  intent_review: 'Intent review', single_transaction: 'Single-transaction limit', period_budget: 'Period budget',
};
