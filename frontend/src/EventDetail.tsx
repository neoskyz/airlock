import { Fragment, useEffect, useState } from 'react';
import { ApiError, request } from './api';
import type { AuditEvent, AuditRow } from './types';
import { ConsoleLink, eventDate, eventMoney, eventPath, policyNames } from './consoleNavigation';
import './consoleEvents.css';

export function EventDecision({ row }: { row: AuditRow }) {
  const computed = row.computed_decision || row.decision;
  const monitor = row.mode === 'Monitor' || !row.firewall_enabled;
  const label = row.display_decision || (monitor && computed === 'BLOCK' ? 'Would have blocked' : monitor && computed === 'REQUIRE_APPROVAL' ? 'Would require approval' : row.decision === 'BLOCK' ? 'Blocked' : row.decision === 'REQUIRE_APPROVAL' ? 'Approval required' : 'Allowed');
  return <span className={`badge ${monitor && computed !== 'ALLOW' ? 'monitor' : row.decision === 'BLOCK' ? 'red' : row.decision === 'REQUIRE_APPROVAL' ? 'amber' : 'green'}`}>{label}</span>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><dt>{label}</dt><dd>{children ?? 'Not recorded'}</dd></div>;
}
function Highlight({ text, excerpt }: { text: string; excerpt?: string }) {
  if (!excerpt || !text.includes(excerpt)) return <>{text}</>;
  const parts = text.split(excerpt);
  return <>{parts.map((part, index) => <Fragment key={index}>{index > 0 && <mark>{excerpt}</mark>}{part}</Fragment>)}</>;
}
const millis = (value?: number | null) => value === undefined || value === null ? 'Not recorded' : `${Math.round(value * 100) / 100} ms`;
const yesNo = (value?: boolean | null) => value === undefined || value === null ? 'Not recorded' : value ? 'Yes' : 'No';
export function EventDetail({ id, revision }: { id: string; revision: string }) {
  const [row, setRow] = useState<AuditEvent | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void request<AuditEvent>(`/api/audit/${encodeURIComponent(id)}`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setRow(value);
    }).catch(err => {
      if (!controller.signal.aborted) {
        if (err instanceof ApiError && err.status === 404) setRow(null);
        setError(err instanceof ApiError && err.status === 404 ? 'This audit event was not found. It may have been removed by a sandbox reset.' : `Could not load this audit event: ${(err as Error).message}`);
      }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, revision, retry]);
  const current = row?.id === id ? row : null;
  const review = current?.ai_review_detail;
  const call = current?.call;
  return <div className="event-detail"><div className="event-toolbar"><ConsoleLink href="/console#audit" className="event-link">← Audit log</ConsoleLink><ConsoleLink href="/console/flagged" className="event-link">Flagged events</ConsoleLink><code>{id}</code>{loading && current && <span role="status">Updating…</span>}</div>
    {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
    {!current && loading && <div className="empty-panel" role="status">Loading audit event…</div>}
    {current && <>
      <section className="event-overview event-panel"><div><span className="eyebrow">FINAL DECISION</span><div className="event-decision">{current.mode !== 'Monitor' && current.computed_decision === 'REQUIRE_APPROVAL' && current.enforced_decision === 'ALLOW' ? <span className="badge green">Allowed after approval</span> : <EventDecision row={current}/>}<span className="badge">{current.mode || (current.firewall_enabled ? 'Enforce' : 'Monitor')}</span>{current.mock && <span className="badge amber">MOCK</span>}</div><p>{current.reason}</p><span className="event-muted">{eventDate(current.timestamp)}</span></div><div className="event-risk"><span>Risk score</span><strong>{current.risk_score ?? '—'}<small>/ 100</small></strong><span>Computed: {current.computed_decision || current.decision}</span><span>Execution decision: {current.enforced_decision || 'Not recorded'}</span></div></section>
      <div className="event-two-column"><section className="event-panel"><h2>Call parameters</h2><dl className="event-fields"><Field label="Tool"><code>{call?.tool || current.tool}</code></Field><Field label="Amount">{eventMoney(call?.amount ?? current.amount, call?.currency || current.currency)} · {call?.currency || current.currency}</Field><Field label="Recipient">{call?.payee || current.payee}</Field><Field label="Description">{call ? call.description || 'Empty description' : current.description || 'Not recorded'}</Field><Field label="Category">{call ? call.category || 'Unspecified' : current.category || 'Not recorded'}</Field><Field label="Product ID">{call ? call.product_id || 'Not applicable' : 'Not recorded'}</Field></dl></section>
      <section className="event-panel"><h2>Risk factors</h2>{current.factors?.length ? <div className="event-factors">{current.factors.map((factor, index) => <div key={`${factor.name}-${index}`}><div><strong>{factor.name.replaceAll('_', ' ')}</strong><p>{factor.reason}</p></div><span className={factor.points > 0 ? 'event-risk-points' : ''}>{factor.points > 0 ? '+' : ''}{factor.points}</span></div>)}</div> : <p className="event-muted">No factor breakdown was recorded.</p>}</section></div>
      <section className="event-panel"><h2>Policy results <span>At evaluation</span></h2><div className="event-policy-list">{Object.entries(policyNames).map(([key, title], index) => {
        const check = current.policy_checks?.find(item => item.id === key);
        const outcome = check?.outcome || 'unknown';
        return <div className="event-policy" key={key}><span className="policy-number">0{index + 1}</span><div><h3>{check?.title || title}</h3><p>{check?.reason || 'This event predates policy snapshots. Historical results are not available.'}</p></div><span className={`badge ${outcome === 'pass' ? 'green' : outcome === 'fail' ? 'red' : ''}`}>{outcome === 'pass' ? 'Pass' : outcome === 'fail' ? 'Fail' : outcome === 'disabled' ? 'Disabled' : 'Not recorded'}</span></div>;
      })}</div>{Boolean(current.policy_rechecks?.length) && <details className="event-rechecks"><summary>Policy rechecks ({current.policy_rechecks?.length})</summary><p className="event-muted">Policies were checked again before execution. A later check can change the initial result.</p>{current.policy_rechecks?.map((recheck, index) => <div key={index}><h3>{recheck.stage.replaceAll('_', ' ')}</h3>{recheck.policy_checks.map(check => <div className="event-recheck-row" key={check.id}><strong>{check.title || policyNames[check.id] || check.id}</strong><span className={`badge ${check.outcome === 'pass' ? 'green' : check.outcome === 'fail' ? 'red' : ''}`}>{check.outcome}</span><p>{check.reason}</p></div>)}</div>)}</details>}</section>
      <div className="event-two-column"><section className="event-panel"><h2>AI intent review</h2><dl className="event-fields"><Field label="Review invoked">{yesNo(review?.invoked)}</Field><Field label="Model called">{yesNo(review?.model_invoked)}</Field><Field label="Reviewer">{review?.reviewer === 'rules' ? 'Rules fallback' : review?.reviewer === 'AI' ? 'AI intent judge' : 'None recorded'}</Field><Field label="Status">{review?.status || current.ai_review || 'Not recorded'}{review?.cached ? ' · cached result' : ''}</Field><Field label="Verdict">{review?.decision || 'No verdict'}</Field><Field label="Reason">{review?.reason || (review?.status === 'skipped' ? current.ai_review : 'Not recorded')}</Field><Field label="Latency">{millis(review?.latency_ms ?? current.latency?.ai_review_ms)}</Field></dl>{review?.suspicious_excerpt && <div className="event-excerpt"><span>Suspicious excerpt · {review.source || 'source not recorded'}</span><mark>{review.suspicious_excerpt}</mark></div>}</section>
      <section className="event-panel"><h2>Stage latency</h2><dl className="event-fields"><Field label="Rules">{millis(current.latency?.rules_ms)}</Field><Field label="AI review">{millis(current.latency?.ai_review_ms)}</Field><Field label="Decision total">{millis(current.latency?.total_ms)}</Field><Field label="Approval wait">{millis(current.latency?.approval_ms)}</Field><Field label="PayPal execution">{millis(current.latency?.execution_ms)}</Field><Field label="End to end">{millis(current.latency?.end_to_end_ms)}</Field></dl></section></div>
      <section className="event-panel"><h2>Provenance evidence</h2><p className="event-muted">Original user instructions and untrusted tool output retain their recorded sources.</p>{current.suspicious_excerpt && <div className="event-excerpt"><span>Flagged text · {current.source || 'source not recorded'}</span><mark>{current.suspicious_excerpt}</mark></div>}<div className="event-sources">{current.provenance?.length ? current.provenance.map(source => <article key={source.id} className={source.origin === 'tool' ? 'untrusted' : ''}><header><span className={`badge ${source.origin === 'tool' ? 'amber' : 'blue'}`}>{source.origin === 'tool' ? 'Untrusted tool output' : 'User instruction'}</span><strong>{source.name}</strong><code>{source.id}</code></header><pre><Highlight text={source.text} excerpt={current.suspicious_excerpt || review?.suspicious_excerpt || undefined}/></pre></article>) : <p className="event-muted">No provenance was recorded for this event.</p>}</div></section>
      <div className="event-two-column"><section className="event-panel"><h2>Approval history</h2>{current.approval_history?.length ? <ol className="event-approval-history">{current.approval_history.map((entry, index) => <li key={`${entry.timestamp}-${index}`}><strong>{entry.action.replaceAll('_', ' ')}</strong><span>{entry.actor}</span><time dateTime={entry.timestamp}>{eventDate(entry.timestamp)}</time></li>)}</ol> : <p className="event-muted">{current.mode === 'Monitor' && current.computed_decision === 'REQUIRE_APPROVAL' ? 'Monitor would require approval; execution proceeded without waiting for a person.' : current.status === 'awaiting_approval' ? 'Awaiting a response from the sandbox console operator.' : 'No approval actions were recorded.'}</p>}</section>
      <section className="event-panel"><h2>PayPal execution</h2><dl className="event-fields"><Field label="Execution status">{current.status.replaceAll('_', ' ')}</Field><Field label="PayPal status">{current.paypal_status || (['unknown', 'executing'].includes(current.status) ? 'Not confirmed' : 'No request submitted')}</Field><Field label="PayPal ID"><code>{current.paypal_id || '—'}</code>{current.mock && current.paypal_id && <span className="badge amber">MOCK</span>}</Field></dl><p className="event-muted">Created orders are not captured payments. Accepted payout batches may remain pending.</p>{current.execution_result && <details className="event-json"><summary>Provider response</summary><pre>{JSON.stringify(current.execution_result, null, 2)}</pre></details>}</section></div>
      <section className="event-panel"><h2>Other events in this run</h2><p className="event-muted">Run <code>{current.run_id}</code></p>{current.run_events?.length ? <div className="event-run-list">{current.run_events.map(event => <ConsoleLink key={event.id} href={eventPath(event.id)}><span><code>{event.tool}</code><small>{eventDate(event.timestamp)}</small></span><strong>{eventMoney(event.amount, event.currency)}</strong><span>{event.display_decision || event.decision}</span><span aria-hidden="true">→</span></ConsoleLink>)}</div> : <p className="event-muted">No other audit events in this run.</p>}</section>
      <details className="event-json event-panel"><summary>Raw event JSON</summary><pre>{JSON.stringify(current, null, 2)}</pre></details>
    </>}
  </div>;
}
