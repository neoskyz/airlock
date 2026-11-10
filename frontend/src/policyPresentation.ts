export function validRecipientAddress(value: string): boolean {
  if (value.length > 254 || /\s/.test(value)) return false;
  const domain = value.includes('@') ? value.split('@')[1] : value;
  if (value.includes('@') && !/^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+$/i.test(value)) return false;
  return Boolean(domain && domain.length <= 253 && domain.split('.').length >= 2 && domain.split('.').every(part => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(part)) && /[a-z]{2,}$/i.test(domain));
}
export function recipientLabel(address: string): string {
  return address.includes('@') ? address.split('@')[0] : address.split('.')[0];
}
export const riskWeightLabels: Record<string, string> = {
  unknown_payee: 'Unknown payee', unknown_merchant: 'Unknown merchant', new_payee: 'First payment to an allowed payee',
  not_requested: 'Not requested by user', negated: 'User declined payment', over_budget: 'Above user budget',
  above_auto_approve_limit: 'Above auto-approve limit', payout_tool: 'Payout tool', velocity: 'Payment velocity',
  unknown_merchant_velocity: 'Unknown merchant velocity', untrusted_source: 'Untrusted source', category: 'Missing product category',
};
