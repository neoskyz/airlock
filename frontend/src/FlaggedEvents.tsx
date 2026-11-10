import { useEffect, useState } from 'react';
import { request } from './api';
import type { FlaggedAudit } from './types';
import { EventDecision } from './EventDetail';
import { ConsoleLink, eventDate, eventMoney, eventPath, policyNames } from './consoleNavigation';
import './consoleEvents.css';

export function FlaggedEvents({ revision }: { revision: string }) {
  const [type, setType] = useState('');
  const [policy, setPolicy] = useState('');
  const [result, setResult] = useState<FlaggedAudit | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const query = new URLSearchParams();
    if (type) query.set('type', type);
    if (policy) query.set('policy', policy);
    void request<FlaggedAudit>(`/api/audit/flagged?${query}`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setResult(value);
    }).catch(err => { if (!controller.signal.aborted) setError(`Could not load flagged events: ${(err as Error).message}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [type, policy, revision, retry]);
  function filter(kind: 'type' | 'policy', value: string) {
    setResult(null);
    if (kind === 'type') setType(value); else setPolicy(value);
  }
  const totals = result?.stats.totals_by_currency;
  const totalLabel = totals && Object.keys(totals).length ? Object.entries(totals).map(([currency, amount]) => eventMoney(amount, currency)).join(' · ') : eventMoney(result?.stats.total_amount || 0, result?.stats.currency || 'USD');
  return <div className="flagged-page">
    <div className="flagged-filters"><label>Decision type<select value={type} onChange={event => filter('type', event.target.value)}><option value="">All flagged decisions</option><option value="BLOCK">Block / would have blocked</option><option value="REQUIRE_APPROVAL">Approval / would require approval</option></select></label><label>Policy<select value={policy} onChange={event => filter('policy', event.target.value)}><option value="">All policies</option>{Object.entries(policyNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>{(type || policy) && <button className="button secondary" onClick={() => { setResult(null); setType(''); setPolicy(''); }}>Clear filters</button>}</div>
    {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
    <div className="flagged-summary"><div><span>Flagged events</span><strong>{result ? result.stats.count : '—'}</strong></div><div><span>Total flagged amount</span><strong>{result ? totalLabel : '—'}</strong></div><p>Includes Monitor decisions and all approval outcomes. Amounts describe proposed calls; they do not represent settled payments.</p></div>
    <div className="flagged-table-heading"><h2>Flagged by policies</h2><span role="status">{loading ? result ? 'Updating…' : 'Loading…' : 'Newest first'}</span></div>
    <div className="flagged-table-wrap"><table className="flagged-table"><thead><tr><th>Time / tool</th><th>Amount / recipient</th><th>Decision / mode</th><th>Triggered policies</th><th>Approval / execution</th><th>Event</th></tr></thead><tbody>{result?.rows.map(row => {
      const checks = [...(row.policy_checks || []), ...(row.policy_rechecks || []).flatMap(item => item.policy_checks)];
      const triggered = new Map(checks.filter(check => check.outcome === 'fail').map(check => [check.id, check.title || policyNames[check.id] || check.id]));
      if (row.policy && !triggered.has(row.policy)) triggered.set(row.policy, policyNames[row.policy] || row.policy);
      const lastApproval = row.approval_history?.at(-1);
      return <tr key={row.id} onClick={event => { if ((event.target as HTMLElement).closest('a,button')) return; const link = event.currentTarget.querySelector<HTMLAnchorElement>('a'); link?.click(); }}><td><time dateTime={row.timestamp}>{eventDate(row.timestamp)}</time><code>{row.tool}</code></td><td><strong>{eventMoney(row.amount, row.currency)}</strong><span>{row.payee}</span></td><td><EventDecision row={row}/><span>{row.mode || (row.firewall_enabled ? 'Enforce' : 'Monitor')}</span>{row.computed_decision === 'REQUIRE_APPROVAL' && row.decision === 'BLOCK' && <small>Originally required approval</small>}</td><td>{triggered.size ? [...triggered].map(([id, title]) => <span key={id}>{title}</span>) : <span>{policyNames[row.policy || ''] || row.policy || 'Not recorded'}</span>}</td><td><strong>{lastApproval ? lastApproval.action.replaceAll('_', ' ') : row.status === 'awaiting_approval' ? 'Awaiting approval' : row.mode === 'Monitor' ? 'Observed in Monitor' : row.computed_decision === 'REQUIRE_APPROVAL' ? 'Approval history not recorded' : 'No approval required'}</strong><span>{row.status.replaceAll('_', ' ')}</span>{row.paypal_id && <code>{row.paypal_id}</code>}</td><td><ConsoleLink className="event-link" href={eventPath(row.id)} aria-label={`View audit event ${row.id}`}>View details →</ConsoleLink></td></tr>;
    })}</tbody></table>{!loading && result?.rows.length === 0 && <div className="empty-panel"><h3>No flagged events</h3><p>{type || policy ? 'No events match these filters.' : 'Blocked and approval-required decisions will appear here.'}</p></div>}{loading && !result && <div className="empty-panel" role="status">Loading flagged events…</div>}</div>
  </div>;
}
