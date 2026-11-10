import type { AnchorHTMLAttributes, MouseEvent } from 'react';

export function navigateConsole(path: string) {
  if (`${location.pathname}${location.hash}` === path) return;
  history.pushState(null, '', path);
  dispatchEvent(new Event('airlock:navigate'));
}
export function eventPath(id: string) { return `/console/events/${encodeURIComponent(id)}`; }
export function ConsoleLink({ href = '/console', onClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  function follow(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || props.target === '_blank') return;
    event.preventDefault();
    navigateConsole(href);
  }
  return <a {...props} href={href} onClick={follow}/>;
}
export const policyNames: Record<string, string> = {
  single_transaction: 'Single-transaction limit', period_budget: 'Period budget',
  allowlist: 'Merchant / payee allowlist', categories: 'Category restrictions', risk_threshold: 'Risk score thresholds',
};
export function eventMoney(value: string | number | undefined, currency = 'USD') {
  try { return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(Number(value || 0)); }
  catch { return `${currency || 'Unspecified currency'} ${Number(value || 0).toFixed(2)}`; }
}
export function eventDate(value?: string) { return value ? new Date(value).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'medium' }) : 'Not recorded'; }
