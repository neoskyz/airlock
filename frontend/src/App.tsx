import { useEffect, useMemo, useRef, useState } from 'react';
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';
import { ApiError, request } from './api';
import { displayText, isRunActive, RUN_UNAVAILABLE } from './runUpdates';
import type { Approval, AuditRow, Config, Explainable, LiveState, Mode, Policy, Product, Run, TimelineEvent } from './types';

ModuleRegistry.registerModules([AllCommunityModule]);
const purchase = 'Buy me the cheapest 65W USB-C charger under $40';
const contractor = "Pay my contractor's $120 invoice";
const money = (value: string | number | undefined) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0));
const time = (value: string) => new Date(value).toLocaleTimeString('en-US', { hour12: false });
const neutral = (text = '') => displayText(text);
const initial: LiveState = { config: { mode: 'Monitor', demo_mode: true, agent_mode: 'compromised', live_available: false, llm_degraded: false, paypal_mode: 'MOCK', policy_yaml: '' }, runs: [], audit: { rows: [], stats: { blocked_amount: 0, allowed_amount: 0 } }, wallet: { balance: 0, currency: 'USD', label: 'Sandbox · local balance', source: 'local_ledger' } };
const gridTheme = themeQuartz.withParams({ backgroundColor: '#121821', foregroundColor: '#dce3ed', headerBackgroundColor: '#19212d', headerTextColor: '#8f9db1', borderColor: '#293344', accentColor: '#8b9aff', fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif', fontSize: 15, rowHoverColor: '#ffffff05', wrapperBorderRadius: 10 });

type IconName = 'shield' | 'feed' | 'approval' | 'policy' | 'audit' | 'docs' | 'arrow' | 'check' | 'search' | 'bag' | 'wallet' | 'stop' | 'external' | 'copy' | 'close';
function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    shield: <><path d="m12 3 8 3v6c0 4-8 9-8 9s-8-5-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    feed: <><path d="M3 12h4l3-7 4 14 3-7h4"/></>, approval: <><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M9 13l2 2 4-4"/></>,
    policy: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></>,
    audit: <><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 10v10"/></>, docs: <><path d="M4 4h6a2 2 0 0 1 2 2v15a4 4 0 0 0-4-2H4V4ZM20 4h-6a2 2 0 0 0-2 2v15a4 4 0 0 1 4-2h4V4Z"/></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5"/></>, check: <path d="m5 12 4 4 10-10"/>, search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></>, bag: <><path d="M5 8h14l1 13H4L5 8Z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/></>,
    wallet: <><rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 8h18m0 4h-6v5h6"/><path d="M17 14.5h.1"/></>, stop: <rect x="7" y="7" width="10" height="10" rx="1"/>, external: <><path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6"/></>, copy: <><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v12h5"/></>, close: <path d="m6 6 12 12M18 6 6 18"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
function useLive(audience: 'client' | 'console') {
  const [state, setState] = useState(initial);
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting'>('connecting');
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const source = new EventSource(`/api/events?audience=${audience}`);
    source.addEventListener('state', event => {
      try {
        const value = JSON.parse((event as MessageEvent).data) as LiveState;
        if (!alive) return;
        setState(current => ({ ...current, ...value, config: { ...current.config, ...value.config } }));
        setConnection('live'); setError('');
      } catch { setError('The live update could not be read. Reconnecting to the server…'); }
    });
    source.onopen = () => { if (alive) setConnection('live'); };
    source.onerror = () => { if (alive) setConnection('reconnecting'); };
    return () => { alive = false; source.close(); };
  }, [audience]);
  return { state, connection, error };
}
function Connection({ state }: { state: string }) { return <span className={`connection ${state}`}><i/>{state === 'live' ? 'Live sync' : state === 'connecting' ? 'Connecting' : 'Reconnecting'}</span>; }
function Badge({ children, tone = '' }: { children: React.ReactNode; tone?: string }) { return <span className={`badge ${tone}`}>{children}</span>; }
function ErrorBanner({ message, onClose }: { message: string; onClose?: () => void }) { return message ? <div className="error-banner" role="alert"><span>{neutral(message)}</span>{onClose && <button aria-label="Dismiss error" onClick={onClose}><Icon name="close" size={16}/></button>}</div> : null; }
function ProductImage({ product, large = false }: { product: Product; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  const url = product.image || product.image_url;
  return <div className={`product-image ${large ? 'large' : ''}`}>{url && !failed ? <img src={url} alt={product.name} onError={() => setFailed(true)} loading="lazy"/> : <div className="charger-illustration"><span/><i/><b/></div>}</div>;
}
function ProductCard({ product }: { product: Product }) {
  return <article className="product-card"><ProductImage product={product}/><div className="product-card-body"><span className="merchant-domain">{product.domain || product.merchant}</span><h4>{product.title || product.name}</h4><div className="product-bottom"><strong>{money(product.price)}</strong><span>{product.category || 'Electronics'}</span></div></div></article>;
}
function ClientEvent({ event }: { event: TimelineEvent }) {
  if (event.products?.length) return <div className="catalog-result"><div className="trace-step"><span className="step-icon"><Icon name="search" size={18}/></span><div><h3>Catalog search</h3><p>{event.products.length} products found for your request</p></div><span className="step-status">Complete</span></div><div className="product-grid">{event.products.map(product => <ProductCard key={product.id} product={product}/>)}</div><div className="catalog-credit">Product catalog by <strong>Channel3</strong></div></div>;
  if (event.product) return <div className="storefront"><div className="browser-bar"><i/><i/><i/><span>{event.product.domain || event.product.merchant}{event.product.url ? ' / product' : ''}</span><Icon name="external" size={14}/></div><div className="storefront-content"><ProductImage product={event.product} large/><div><span className="storefront-brand">{event.product.domain || event.product.merchant}</span><h3>{event.product.title || event.product.name}</h3><p>{event.product.description}</p><strong>{money(event.product.price)}</strong><span className="storefront-stock"><i/>Available for checkout</span></div></div></div>;
  if (event.type === 'decision' && !['blocked', 'awaiting_approval'].includes(event.status || '')) return null;
  if (event.type === 'provenance') return null;
  const blocked = event.status === 'blocked' || event.status === 'denied' || event.decision === 'BLOCK';
  const pending = event.status === 'awaiting_approval';
  const success = Boolean(event.paypal_id);
  const title = success ? event.tool === 'create_order' ? 'PayPal order created' : 'PayPal payout submitted' : pending ? 'Awaiting payment approval' : blocked ? 'Payment tool rejected' : event.type === 'checkout' ? 'Preparing checkout' : event.type === 'searching' ? 'Searching catalog' : event.type === 'cancelled' ? 'Task stopped' : event.type === 'product_page' ? 'Opening product page' : 'Agent activity';
  return <article className={`trace-step standalone ${blocked ? 'rejected' : pending ? 'pending' : ''}`}><span className="step-icon"><Icon name={success ? 'check' : event.type === 'searching' ? 'search' : pending ? 'approval' : 'bag'} size={18}/></span><div className="trace-text"><h3>{title}</h3><p>{pending ? 'The payment request is waiting for a response. The agent will continue automatically.' : blocked ? 'The payment tool rejected this request. No PayPal call was made.' : neutral(event.message || event.reason || '')}</p>{event.paypal_id && <div className="payment-receipt"><code>{event.paypal_id}</code><Badge>{event.paypal_status || 'ACCEPTED'}</Badge>{event.mock && <Badge tone="amber">MOCK</Badge>}</div>}{event.amount !== undefined && <span className="trace-amount">{money(event.amount)}{event.payee ? ` → ${event.payee}` : ''}</span>}</div>{success && <span className="step-status">Accepted</span>}</article>;
}
function Client() {
  const { state, connection, error: liveError } = useLive('client');
  const { config, runs, wallet } = state;
  const [input, setInput] = useState(purchase);
  const [selected, setSelected] = useState(() => sessionStorage.getItem('airlock-client-run'));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { document.title = 'Agentic Payment Client'; }, []);
  const runningRun = runs.find(run => isRunActive(run.status));
  const run = runs.find(item => item.id === selected) || runningRun || runs.at(-1);
  const active = busy || Boolean(runningRun);
  useEffect(() => {
    if (!selected || connection !== 'live' || runs.some(item => item.id === selected)) return;
    let stale = false;
    void request<Run>(`/api/runs/${selected}`, { signal: AbortSignal.timeout(10000) }).catch(err => {
      if (!stale && err instanceof ApiError && err.status === 404) {
        sessionStorage.removeItem('airlock-client-run'); setSelected(null); setError(RUN_UNAVAILABLE);
      }
    });
    return () => { stale = true; };
  }, [selected, runs, connection]);
  useEffect(() => { if (run?.events.length) end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [run?.events.length]);
  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!input.trim() || active || connection !== 'live') return;
    setBusy(true); setError('');
    try {
      const { id } = await request<{id: string}>('/api/runs', { method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify({ message: input.trim() }) });
      setSelected(id); sessionStorage.setItem('airlock-client-run', id);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function stop() {
    if (!runningRun || busy) return;
    setBusy(true);
    try { await request(`/api/runs/${runningRun.id}/cancel`, { method: 'POST', signal: AbortSignal.timeout(10000) }); }
    catch (err) { setError(err instanceof ApiError && err.status === 404 ? RUN_UNAVAILABLE : (err as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="client-app"><header className="client-header"><a href="/client" className="client-brand"><span className="client-symbol"><Icon name="bag" size={23}/></span><span>Agentic Payment Client<small>Agent workspace</small></span></a><div className="client-header-right"><Connection state={connection}/><div className="wallet"><span className="wallet-icon"><Icon name="wallet" size={22}/></span><div><span>PayPal wallet balance</span><strong>{connection === 'connecting' ? '—' : money(wallet.balance)}</strong><small>{wallet.label || 'Sandbox · local balance'}{config.paypal_mode === 'MOCK' ? ' · MOCK' : ''}</small></div></div></div></header><main className="client-main"><div className="client-intro"><span className="eyebrow">PAYMENTS WORKSPACE</span><h1>What would you like to do?</h1><p>Find products and send payments through your agent.</p></div><ErrorBanner message={error || liveError} onClose={() => setError('')}/><form className="task-composer" onSubmit={start}><label htmlFor="task">Task</label><div className="task-input-row"><input id="task" value={input} onChange={event => setInput(event.target.value)} placeholder="Describe a purchase or payment…" disabled={active} maxLength={4000}/>{active ? <button type="button" className="button stop" onClick={() => void stop()} disabled={busy || !runningRun}><Icon name="stop" size={17}/>Stop</button> : <button className="button primary" disabled={!input.trim() || connection !== 'live'}>Start<Icon name="arrow" size={18}/></button>}</div></form><div className="prompt-examples"><span>Examples</span>{[purchase, contractor].map(prompt => <button key={prompt} onClick={() => setInput(prompt)} disabled={active}>{prompt}<span>↗</span></button>)}</div><section className="activity-section"><div className="section-heading"><div><h2>Activity</h2><span>{run ? `Task ${run.id.slice(0, 8)}` : 'Your agent’s work will appear here'}</span></div>{(run?.agent_mode === 'compromised' || (!run && config.demo_mode)) && <Badge>Scripted agent</Badge>}{run && <Badge tone={isRunActive(run.status) ? 'blue' : ''}>{run.status.replaceAll('_', ' ')}</Badge>}</div>{run ? <><div className="request-summary"><span>You</span><p>{run.messages?.find(item => item.role === 'user')?.content || input}</p></div><div className="activity-trace">{run.events?.map(event => <ClientEvent key={event.id} event={event}/>)}{isRunActive(run.status) && <div className="agent-working"><i/><span>{run.status === 'awaiting_approval' ? 'Waiting for payment approval…' : 'Agent is working…'}</span></div>}{run.status === 'completed' && <div className="task-finished"><Icon name="check" size={17}/>Task finished<span>Orders and payouts retain their PayPal status; acceptance does not confirm settlement.</span></div>}{run.status === 'cancelled' && <div className="task-finished">Task stopped. Submitted requests are not rolled back.</div>}{run.error && <ErrorBanner message="The agent could not complete this task. Check the payment status before retrying."/>}<div ref={end}/></div></> : <div className="activity-empty"><div className="empty-circles"><Icon name="search"/><span/><Icon name="bag"/><span/><Icon name="check"/></div><h3>Ready for a task</h3><p>Catalog search, checkout, and payment results appear as they happen.</p></div>}</section><footer className="client-footer"><span>PayPal sandbox{config.paypal_mode === 'MOCK' ? ' · MOCK payment adapter' : ''}</span><span>Sandbox balance is tracked locally from submitted requests.</span><a href="/console" target="_blank" rel="noreferrer">Open security console<Icon name="external" size={13}/></a></footer></main></div>;
}
function DecisionBadge({ row }: { row: AuditRow }) {
  const monitor = row.mode === 'Monitor' || !row.firewall_enabled;
  const would = monitor && row.decision !== 'ALLOW';
  return <Badge tone={would ? 'monitor' : row.decision === 'BLOCK' ? 'red' : row.decision === 'REQUIRE_APPROVAL' ? 'amber' : 'green'}>{row.display_decision || (would ? row.decision === 'BLOCK' ? 'Would have blocked' : 'Would require approval' : row.decision === 'REQUIRE_APPROVAL' ? 'Approval required' : row.decision === 'ALLOW' ? 'Allowed' : 'Blocked')}</Badge>;
}
function RiskScore({ value = 0 }: { value?: number }) { return <div className={`risk-score ${value >= 85 ? 'high' : value >= 60 ? 'medium' : ''}`}><strong>{value}</strong><span>/ 100</span></div>; }
function Factors({ item }: { item: Explainable }) {
  return <div className="factor-list">{item.factors?.map((factor, index) => <div className="factor" key={`${factor.name}-${index}`}><div><span>{factor.name.replaceAll('_', ' ')}</span><p>{neutral(factor.reason)}</p></div><strong className={factor.points > 0 ? 'positive' : ''}>{factor.points > 0 ? '+' : ''}{factor.points}</strong></div>)}</div>;
}
function LatencyLine({ row }: { row: Explainable }) {
  return <span className="latency-line">rules {Math.round(row.latency?.rules_ms || 0)} ms<span>·</span>AI review {row.ai_review?.includes('skip') ? neutral(row.ai_review) : `${Math.round(row.latency?.ai_review_ms || 0)} ms`}{row.ai_review && !row.ai_review.includes('skip') && <span className="latency-note">{neutral(row.ai_review)}</span>}</span>;
}
function ExecutionStatus({ row }: { row: AuditRow }) {
  const accepted = row.status === 'succeeded' && Boolean(row.paypal_id);
  const label = accepted ? row.tool === 'create_order' ? 'PayPal order created' : 'PayPal payout submitted'
    : row.status === 'unknown' ? 'Provider outcome unknown · verify sandbox'
    : row.status === 'executing' ? 'Submitting to PayPal…'
    : ['blocked', 'denied', 'cancelled'].includes(row.status) ? 'No PayPal call made'
    : row.status === 'awaiting_approval' ? 'Awaiting approval · no PayPal call made' : row.status.replaceAll('_', ' ');
  return <div className={`feed-execution ${accepted ? 'accepted' : ''}`}><span>{label}</span>{accepted && <><code>{row.paypal_id}</code><Badge>{row.paypal_status || 'ACCEPTED'}</Badge>{row.mock && <Badge tone="amber">MOCK</Badge>}</>}</div>;
}
function FeedCard({ row, selected, onToggle }: { row: AuditRow; selected: boolean; onToggle: () => void }) {
  return <article className={`feed-card ${row.decision.toLowerCase()} ${row.mode === 'Monitor' ? 'monitor' : ''}`}><button className="feed-card-summary" onClick={onToggle} aria-expanded={selected}><span className="feed-tool-icon"><Icon name={row.tool === 'create_order' ? 'bag' : 'arrow'} size={19}/></span><div className="feed-tool"><code>{row.tool}</code><span>{time(row.timestamp)}</span></div><div className="feed-payment"><strong>{money(row.amount)}</strong><span>{row.payee}</span></div><DecisionBadge row={row}/><div className="feed-risk"><span>Risk score</span><RiskScore value={row.risk_score}/></div><span className="expand-chevron">{selected ? '⌃' : '⌄'}</span></button><div className="feed-card-context"><span>{neutral(row.reason)}</span><LatencyLine row={row}/><ExecutionStatus row={row}/></div>{selected && <div className="feed-details"><div className="risk-breakdown"><div className="detail-heading">Risk factors<span>Points</span></div><Factors item={row}/><div className="policy-fired"><span>Policy</span><strong>{row.policy?.replaceAll('_', ' ') || 'Risk score threshold'}</strong></div></div><div className="evidence-panel"><div className="detail-heading">Provenance & execution</div>{row.suspicious_excerpt ? <div className="provenance-evidence"><span className="evidence-origin">Untrusted tool output · {row.source || 'merchant product page'}</span><mark>{row.suspicious_excerpt}</mark></div> : <div className="no-taint"><Icon name="check" size={16}/><span>No suspicious excerpt identified</span></div>}<div className="execution-fields"><div><span>Execution</span><strong>{row.status.replaceAll('_', ' ')}</strong></div><div><span>PayPal status</span><strong>{row.paypal_status || (['unknown', 'executing'].includes(row.status) ? 'Not confirmed' : 'No request submitted')}</strong></div><div><span>PayPal ID</span><code>{row.paypal_id || '—'}{row.mock && row.paypal_id ? ' · MOCK' : ''}</code></div><div><span>Mode at evaluation</span><strong>{row.mode || (row.firewall_enabled ? 'Enforce' : 'Monitor')}</strong></div></div></div></div>}</article>;
}
const policyDefinitions = [
  { key: 'single_transaction', title: 'Single-transaction limit', description: 'Block a payment when its amount exceeds the per-transaction limit.', tag: 'Hard limit' },
  { key: 'period_budget', title: 'Period budget', description: 'Block payments that exceed the daily or weekly budget. Pending approvals reserve funds.', tag: 'Hard limit' },
  { key: 'allowlist', title: 'Merchant / payee allowlist', description: 'Unknown recipients add risk. Enable hard-block mode to reject them immediately.', tag: 'Recipient trust' },
  { key: 'categories', title: 'Category restrictions', description: 'Block restricted product categories using catalog metadata.', tag: 'Hard limit' },
  { key: 'risk_threshold', title: 'Risk score thresholds', description: 'Require human approval or block based on the final explainable risk score.', tag: 'Human review' },
];
function Policies({ config, onError, onReset, resetBusy, canReset }: { config: Config; onError: (error: string) => void; onReset: () => void; resetBusy: boolean; canReset: boolean }) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => { void request<Policy>('/api/policies').then(setPolicy).catch(err => onError((err as Error).message)); }, [config.policy_yaml, onError]);
  async function update(changes: Partial<Policy>, key: string) {
    if (!policy || saving) return;
    setSaving(true); setSaved('');
    try { const value = await request<Policy>('/api/policies', { method: 'PATCH', body: JSON.stringify({ ...policy, ...changes }) }); setPolicy(value); setSaved(key); setDrafts({}); }
    catch (err) { onError(`Policy update failed: ${(err as Error).message}`); }
    finally { setSaving(false); }
  }
  function field(key: string, label: string, value: string | number, commit: (text: string) => Partial<Policy>, type = 'number') {
    return <label className="policy-field">{label}<input aria-label={label} type={type} min={type === 'number' ? '0' : undefined} step={type === 'number' ? '0.01' : undefined} value={drafts[key] ?? value} disabled={saving} onChange={event => setDrafts(current => ({ ...current, [key]: event.target.value }))} onBlur={() => { const text = drafts[key]; if (text !== undefined && text !== String(value)) void update(commit(text), key); }} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}/></label>;
  }
  if (!policy) return <div className="empty-panel">Loading policies…</div>;
  return <div className="policies-list">{policyDefinitions.map((definition, index) => <section key={definition.key} className={`policy-card ${policy.enabled?.[definition.key] === false ? 'disabled' : ''}`}><div className="policy-card-header"><span className="policy-number">0{index + 1}</span><div><h3>{definition.title}</h3><p>{definition.description}</p></div><Badge>{definition.tag}</Badge><button className={`toggle ${policy.enabled?.[definition.key] !== false ? 'on' : ''}`} role="switch" aria-checked={policy.enabled?.[definition.key] !== false} aria-label={`Enable ${definition.title}`} disabled={saving} onClick={() => void update({ enabled: { ...policy.enabled, [definition.key]: policy.enabled?.[definition.key] === false } }, definition.key)}><span/></button></div><div className="policy-values">{definition.key === 'single_transaction' && field('single_transaction_limit', 'Maximum amount · USD', policy.single_transaction_limit, value => ({ single_transaction_limit: value }))}{definition.key === 'period_budget' && <>{field('daily_limit', 'Budget amount · USD', policy.daily_limit, value => ({ daily_limit: value }))}<label className="policy-field">Budget period<select value={policy.period} disabled={saving} onChange={event => void update({ period: event.target.value as Policy['period'] }, definition.key)}><option value="daily">Daily · UTC</option><option value="weekly">Weekly · UTC</option></select></label></>}{definition.key === 'allowlist' && <>{field('allowed_payees', 'Allowed recipients · comma separated', policy.allowed_payees.join(', '), value => ({ allowed_payees: value.split(',').map(item => item.trim()).filter(Boolean) }), 'text')}<label className="checkbox-field"><input type="checkbox" checked={policy.unknown_payee_action === 'BLOCK'} disabled={saving} onChange={event => void update({ unknown_payee_action: event.target.checked ? 'BLOCK' : 'REQUIRE_APPROVAL' }, definition.key)}/>Hard-block unknown payees</label></>}{definition.key === 'categories' && field('blocked_categories', 'Restricted categories · comma separated', policy.blocked_categories.join(', '), value => ({ blocked_categories: value.split(',').map(item => item.trim()).filter(Boolean) }), 'text')}{definition.key === 'risk_threshold' && <>{field('risk_approval_threshold', 'Require approval at score', policy.risk_approval_threshold, value => ({ risk_approval_threshold: Number(value) }))}{field('risk_block_threshold', 'Block at score', policy.risk_block_threshold, value => ({ risk_block_threshold: Number(value) }))}{field('auto_approve_threshold', 'Amount risk reference · USD', policy.auto_approve_threshold, value => ({ auto_approve_threshold: value }))}</>}</div></section>)}<div className="policy-save-note"><Icon name="check" size={16}/><span>{saving ? 'Applying policy…' : saved ? 'Policy updated. The change is effective immediately and recorded in the audit log.' : 'Edits apply when you leave a field. All policy changes are audited.'}</span></div><section className="sandbox-reset"><div><h3>Sandbox data</h3><p>Clear local audit history, period budget usage, wallet ledger, and completed tasks before recording. Submitted PayPal requests remain in the sandbox.</p>{!canReset && !resetBusy && <p className="reset-hint">Finish or stop the active task before resetting.</p>}</div><button type="button" className="button secondary reset-button" disabled={!canReset || resetBusy || saving} onClick={onReset}>{resetBusy ? 'Resetting…' : 'Reset sandbox data'}</button></section></div>;
}
function ApprovalInbox({ approvals, rows, busy, onAnswer, selected }: { approvals: Approval[]; rows: AuditRow[]; busy: string; onAnswer: (id: string, approved: boolean) => void; selected: string }) {
  if (!approvals.length) return <div className="empty-panel"><span className="empty-symbol"><Icon name="approval" size={27}/></span><h3>No pending approvals</h3><p>Payment requests that need human review will appear here.</p></div>;
  return <div className="approval-list">{approvals.map(approval => {
    const row = rows.find(item => item.id === approval.id);
    const explanation = { ...row, ...approval };
    return <section id={`approval-${approval.id}`} key={approval.id} className={`approval-card ${selected === approval.id ? 'highlighted' : ''}`}><div className="approval-head"><Badge tone="amber">Pending approval</Badge><span>{approval.tool}</span></div><div className="approval-main"><div><h3>{money(approval.amount)}</h3><p>to <strong>{approval.payee}</strong></p></div><div><span className="muted-label">Risk score</span><RiskScore value={explanation.risk_score}/></div></div><div className="approval-user-context"><span>User request</span><p>{approval.user_request || approval.provenance?.find(source => source.origin === 'user')?.text || row?.provenance?.find(source => source.origin === 'user')?.text || 'See the originating task for authorization.'}</p></div><p className="approval-reason">{neutral(approval.reason)}</p><div className="approval-breakdown"><Factors item={explanation}/></div>{row?.suspicious_excerpt && <div className="provenance-evidence"><span className="evidence-origin">{row.source}</span><mark>{row.suspicious_excerpt}</mark></div>}<div className="approval-footer"><span>The request executes once after approval.</span><div><button className="button secondary" disabled={Boolean(busy)} onClick={() => onAnswer(approval.id, false)}>Deny</button><button className="button primary" disabled={Boolean(busy)} onClick={() => onAnswer(approval.id, true)}><Icon name="check" size={17}/>{busy === approval.id ? 'Submitting…' : 'Approve payment'}</button></div></div></section>;
  })}</div>;
}
function AuditLog({ rows }: { rows: AuditRow[] }) {
  const [filter, setFilter] = useState('');
  const columns = useMemo<ColDef<AuditRow>[]>(() => [
    { field: 'timestamp', headerName: 'Time', minWidth: 115, valueFormatter: params => time(params.value) },
    { field: 'tool', headerName: 'Tool / event', minWidth: 175 },
    { field: 'amount', headerName: 'Amount', minWidth: 115, valueFormatter: params => money(params.value) },
    { field: 'payee', headerName: 'Payee', minWidth: 230 },
    { field: 'decision', headerName: 'Decision', minWidth: 210, cellRenderer: (params: ICellRendererParams<AuditRow>) => params.data ? <DecisionBadge row={params.data}/> : null },
    { field: 'risk_score', headerName: 'Risk', minWidth: 85 },
    { field: 'mode', headerName: 'Mode', minWidth: 110 },
    { field: 'status', headerName: 'Execution', minWidth: 140 },
    { field: 'paypal_id', headerName: 'PayPal ID', minWidth: 210 },
    { field: 'paypal_status', headerName: 'PayPal status', minWidth: 160 },
    { field: 'reason', headerName: 'Reason', minWidth: 420, valueFormatter: params => neutral(params.value) },
  ], []);
  return <><div className="audit-toolbar"><label><Icon name="search" size={18}/><input placeholder="Search tool, payee, ID, reason…" value={filter} onChange={event => setFilter(event.target.value)} aria-label="Search audit log"/></label><span>{rows.length} events · AG Grid Community</span></div><div className="audit-grid"><AgGridReact<AuditRow> theme={gridTheme} rowData={rows} columnDefs={columns} defaultColDef={{ sortable: true, filter: true, resizable: true, flex: 1 }} quickFilterText={filter} rowHeight={55} headerHeight={48} getRowId={params => params.data.id} rowClassRules={{ 'audit-risk': params => params.data?.decision === 'BLOCK' }} overlayNoRowsTemplate="<span>No audit events yet. Start a task in the client.</span>"/></div><p className="audit-caption">SQLite audit ledger · Decisions reflect the active mode at evaluation. PayPal acceptance does not confirm settlement.</p></>;
}
const navItems: { key: string; name: string; icon: IconName; description: string }[] = [
  { key: 'feed', name: 'Live feed', icon: 'feed', description: 'Payment tool calls, risk assessments, and execution results.' },
  { key: 'approvals', name: 'Approvals', icon: 'approval', description: 'Review payment requests before they reach PayPal.' },
  { key: 'policies', name: 'Policies', icon: 'policy', description: 'Five controls evaluated for every payment tool call.' },
  { key: 'audit', name: 'Audit log', icon: 'audit', description: 'Search decisions, payment execution, and configuration changes.' },
];
function Console() {
  const { state, connection, error: liveError } = useLive('console');
  const { config, runs, audit } = state;
  const [tab, setTab] = useState(() => navItems.some(item => item.key === location.hash.slice(1)) ? location.hash.slice(1) : 'feed');
  const [error, setError] = useState('');
  const [modeBusy, setModeBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetMessage, setResetMessage] = useState('');
  const [answering, setAnswering] = useState('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selectedApproval, setSelectedApproval] = useState('');
  const [dismissed, setDismissed] = useState<string[]>([]);
  const approvals = runs.flatMap(run => (run.approvals || []).map(approval => ({ ...approval, user_request: approval.user_request || run.messages.find(message => message.role === 'user')?.content }))).filter(approval => ['pending', 'awaiting_approval'].includes(approval.status));
  const feedRows = audit.rows.filter(row => ['create_order', 'create_payout'].includes(row.tool));
  const pendingNotification = approvals.find(item => !dismissed.includes(item.id));
  const notificationRow = audit.rows.find(row => row.id === pendingNotification?.id);
  const notificationReasons = [...(pendingNotification?.factors || notificationRow?.factors || [])].filter(factor => factor.points > 0).sort((a, b) => b.points - a.points).slice(0, 3);
  const heading = navItems.find(item => item.key === tab) || navItems[0];
  const canReset = connection === 'live' && !runs.some(run => isRunActive(run.status)) && !answering;
  useEffect(() => {
    if (!runs.length && !audit.rows.length) { setExpanded({}); setSelectedApproval(''); setDismissed([]); }
  }, [runs.length, audit.rows.length]);
  function navigate(key: string) { setTab(key); history.replaceState(null, '', `/console#${key}`); }
  useEffect(() => { document.title = 'Airlock Console'; }, []);
  useEffect(() => { const handler = () => { const value = location.hash.slice(1); if (navItems.some(item => item.key === value)) setTab(value); }; addEventListener('hashchange', handler); return () => removeEventListener('hashchange', handler); }, []);
  async function resetSandbox() {
    if (!canReset || resetBusy) return;
    if (!window.confirm('Reset sandbox data? This clears local audit history, period budget usage, wallet ledger, and tasks. Submitted PayPal orders and payouts are not reversed or deleted.')) return;
    setResetBusy(true); setResetMessage(''); setError('');
    try {
      await request('/api/sandbox/reset', { method: 'POST', body: JSON.stringify({ confirmed: true }), signal: AbortSignal.timeout(15000) });
      setExpanded({}); setSelectedApproval(''); setDismissed([]);
      setResetMessage('Sandbox data reset. Local budget usage and wallet ledger are clear.');
    } catch (err) { setError(`Sandbox reset failed: ${(err as Error).message}`); }
    finally { setResetBusy(false); }
  }
  async function setMode(mode: Mode) {
    if (mode === config.mode || modeBusy) return;
    setModeBusy(true); setError('');
    try { await request('/api/mode', { method: 'PATCH', body: JSON.stringify({ mode }) }); }
    catch (err) { setError((err as Error).message); }
    finally { setModeBusy(false); }
  }
  async function answer(id: string, approved: boolean) {
    if (answering) return;
    setAnswering(id); setError('');
    try { await request(`/api/approvals/${id}`, { method: 'POST', body: JSON.stringify({ approved }) }); }
    catch (err) { setError((err as Error).message); }
    finally { setAnswering(''); }
  }
  return <div className="console-app">
    <aside className="console-sidebar"><a className="airlock-brand" href="/console"><span className="brand-mark"><Icon name="shield" size={25}/></span><span>Airlock<small>agentic payment firewall</small></span></a><div className="workspace-label"><span className="workspace-avatar">S</span><div>Sandbox workspace<small>Payment security</small></div><Badge>SBX</Badge></div><span className="nav-label">WORKSPACE</span><nav>{navItems.map(item => <button className={tab === item.key ? 'active' : ''} key={item.key} onClick={() => navigate(item.key)}><Icon name={item.icon}/>{item.name}{item.key === 'approvals' && approvals.length > 0 && <span className="nav-count">{approvals.length}</span>}</button>)}<div className="nav-divider"/><a href="/docs"><Icon name="docs"/>Docs<Icon name="external" size={14}/></a></nav><div className="sidebar-bottom"><a href="/client" target="_blank" rel="noreferrer"><Icon name="external" size={17}/>Open payment client</a><div className="sidebar-version"><i/>Sandbox environment<span>v{config.version || '0.2.0'}</span></div></div></aside>
    <div className="console-workspace"><header className="console-topbar"><div className="breadcrumb">Workspace<span>/</span><strong>{heading.name}</strong></div><div className="topbar-actions"><Connection state={connection}/><span className="topbar-divider"/><span className="mode-label">Mode</span><div className={`mode-switch ${config.mode?.toLowerCase()}`} role="group" aria-label="Firewall mode">{(['Monitor', 'Enforce'] as const).map(mode => <button key={mode} className={config.mode === mode ? 'active' : ''} disabled={modeBusy || connection !== 'live'} onClick={() => void setMode(mode)}>{mode === 'Enforce' && <Icon name="shield" size={14}/>} {mode}</button>)}</div></div></header>
      <main className="console-main"><div className="page-heading"><div><span className="eyebrow">PAYMENT SECURITY</span><h1>{heading.name}</h1><p>{heading.description}</p></div><Badge tone={config.paypal_mode === 'MOCK' ? 'amber' : 'blue'}>PayPal sandbox{config.paypal_mode === 'MOCK' ? ' · MOCK' : ''}</Badge></div><ErrorBanner message={error || liveError} onClose={() => setError('')}/>{resetMessage && <div className="reset-success" role="status"><Icon name="check" size={17}/><span>{resetMessage}</span><button aria-label="Dismiss reset confirmation" onClick={() => setResetMessage('')}><Icon name="close" size={15}/></button></div>}{connection === 'reconnecting' && <div className="connection-notice">Live sync disconnected. Reconnecting automatically; check the audit log before repeating a payment.</div>}
      {tab === 'feed' && <><div className={`mode-notice ${config.mode?.toLowerCase()}`}><Icon name={config.mode === 'Enforce' ? 'shield' : 'feed'} size={19}/><div><strong>{config.mode === 'Enforce' ? 'Enforce mode' : 'Monitor mode'}</strong><span>{config.mode === 'Enforce' ? 'Policy decisions are enforced before a payment can execute.' : 'All calls execute. Policy decisions are computed and recorded for review.'}</span></div><span className="notice-label">{config.mode === 'Enforce' ? 'Protection active' : 'Observe only'}</span></div><div className="stats-grid"><div className="stat-card"><span>PAYMENT CALLS</span><strong>{feedRows.length}</strong><small>Across all client tasks</small></div><div className="stat-card risk-stat"><span>FLAGGED BY POLICY</span><strong>{feedRows.filter(row => row.decision !== 'ALLOW').length}<small>calls</small></strong><small>{feedRows.filter(row => row.mode === 'Monitor' && row.decision === 'BLOCK').length} would have been blocked in Monitor</small></div><div className="stat-card"><span>AWAITING APPROVAL</span><strong>{approvals.length}<i className={approvals.length ? 'stat-indicator' : 'stat-indicator quiet'}/></strong><small>Human review before execution</small></div></div><div className="feed-section-heading"><h2>Recent tool calls<span>{feedRows.length}</span></h2><span>{config.judge_model ? `Intent review · ${neutral(config.judge_model)}` : 'Hybrid risk scoring'}</span></div><div className="feed-list">{feedRows.length ? feedRows.map((row, index) => <FeedCard row={row} key={row.id} selected={expanded[row.id] ?? index === 0} onToggle={() => setExpanded(current => ({ ...current, [row.id]: !(current[row.id] ?? index === 0) }))}/>) : <div className="empty-panel"><span className="empty-symbol"><Icon name="feed" size={29}/></span><h3>Listening for payment calls</h3><p>Open the payment client in another tab and start a task.</p><a className="button secondary" href="/client" target="_blank" rel="noreferrer">Open payment client<Icon name="external" size={15}/></a></div>}</div></>}
      {tab === 'approvals' && <><div className="approval-context-summary">{approvals.length} pending request{approvals.length === 1 ? '' : 's'}<span>Approve only payments you recognize. Denied requests never reach PayPal.</span></div><ApprovalInbox approvals={approvals} rows={audit.rows} busy={answering} onAnswer={(id, approved) => void answer(id, approved)} selected={selectedApproval}/></>}
      {tab === 'policies' && <Policies config={config} onError={setError} onReset={() => void resetSandbox()} resetBusy={resetBusy} canReset={Boolean(canReset)}/>}{tab === 'audit' && <AuditLog rows={audit.rows}/>}</main>
      <footer className="console-footer"><span className={`render-badge ${config.deployed_on_render ? 'deployed' : ''}`}><span className="render-symbol">▰</span>{config.deployed_on_render ? 'Deployed on Render' : 'Render · local workspace'}</span><span><i/>PayPal sandbox · {config.paypal_mode === 'MOCK' ? 'MOCK adapter' : 'Sandbox adapter'}</span><span className="footer-version">Airlock v{config.version || '0.2.0'}</span></footer></div>
    {pendingNotification && <div className="push-notification" role="status"><div className="push-app"><span className="push-app-icon"><Icon name="shield" size={16}/></span><strong>AIRLOCK</strong><span>now</span><button aria-label="Dismiss notification" onClick={() => setDismissed(current => [...current, pendingNotification.id])}><Icon name="close" size={15}/></button></div><button className="push-content" onClick={() => { navigate('approvals'); setSelectedApproval(pendingNotification.id); setDismissed(current => [...current, pendingNotification.id]); }}><h3>Approve {money(pendingNotification.amount)} to {pendingNotification.payee}?</h3><div className="push-risk"><span>Risk score <strong>{pendingNotification.risk_score ?? notificationRow?.risk_score ?? '—'}</strong></span><span>Human approval required</span></div>{notificationReasons.length ? <ul>{notificationReasons.map(reason => <li key={reason.name}>{neutral(reason.reason || reason.name)}</li>)}</ul> : <p>{neutral(pendingNotification.reason)}</p>}<span className="push-action">Tap to review payment<Icon name="arrow" size={14}/></span></button></div>}
  </div>;
}
function CodeBlock({ code, language = 'python' }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy() { try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { setFailed(true); } }
  return <div className="code-block"><div><span>{language}</span><button onClick={() => void copy()}><Icon name="copy" size={14}/>{copied ? 'Copied' : failed ? 'Select code to copy' : 'Copy'}</button></div><pre><code>{code}</code></pre></div>;
}
const setup = `import os\nfrom firewall import AuditStore, Firewall, Policy, Provenance, ToolCall, build_judge\nfrom airlock.paypal import build_paypal\n\npaypal = build_paypal(os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET"))\ncall = ToolCall(tool="create_order", amount="29.00", payee="shop@chargers.example", description="USB-C charger", category="electronics")\nprovenance = Provenance("Buy me a USB-C charger under $40")`;
const quickstart = `guard = Firewall(Policy.load("policies/default.yaml"), build_judge(os.getenv("ANTHROPIC_API_KEY")), AuditStore("audit.sqlite3"))\nprotected_paypal = guard.wrap_tool(paypal.execute)\nresult = await protected_paypal(call, provenance, mock=paypal.mock)`;
const policyYaml = `currency: USD\nsingle_transaction_limit: 1000\ndaily_limit: 2000\nperiod: weekly\nallowed_payees:\n  - shop@chargers.example\n  - contractor@contractor.example\nunknown_payee_action: REQUIRE_APPROVAL\nblocked_categories: [gift_cards, crypto, gambling]\nauto_approve_threshold: 100\nrisk_approval_threshold: 60\nrisk_block_threshold: 85\nenabled:\n  single_transaction: true\n  period_budget: true\n  allowlist: true\n  categories: true\n  risk_threshold: true`;
const docSections = [{ id: 'quickstart', name: 'Quickstart' }, { id: 'decisions', name: 'How decisions work' }, { id: 'policies', name: 'Policy reference' }, { id: 'audit', name: 'Audit & integrations' }, { id: 'threat-model', name: 'Threat model' }];
function Docs() {
  useEffect(() => { document.title = 'Airlock · SDK documentation'; }, []);
  return <div className="docs-app"><header className="docs-header"><a className="airlock-brand" href="/console"><span className="brand-mark"><Icon name="shield" size={24}/></span>Airlock<span className="docs-header-label">Documentation</span></a><div><Badge>Python SDK</Badge><a href="/console">Console<Icon name="external" size={15}/></a></div></header><div className="docs-layout"><aside className="docs-sidebar"><span className="nav-label">PYTHON SDK</span>{docSections.map(section => <a href={`#${section.id}`} key={section.id}>{section.name}</a>)}<div className="docs-sidebar-note"><Icon name="shield" size={19}/><p>Agent-independent policy and payment protection.</p><span>Python 3.13 · v0.2.0</span></div></aside><main className="docs-content"><div className="docs-breadcrumb">Documentation<span>/</span>Python SDK</div>
    <section id="quickstart"><span className="eyebrow">QUICKSTART</span><h1>Protect your agent in 3 lines</h1><p className="docs-lead">Wrap PayPal Agent Toolkit execution with a reusable payment firewall. Each call receives a policy decision, an explainable risk score, and an audit record.</p><CodeBlock code={quickstart}/><h3>Set up your sandbox adapter</h3><p>Install the project with Python 3.13 and <code>uv sync</code>. Prepare the adapter, reviewed tool call, and user provenance before the three-line wrapper:</p><CodeBlock code={setup}/><div className="docs-note"><Icon name="docs" size={19}/><p><code>build_paypal</code> is this application's sandbox adapter. Its order executor calls the real PayPal Agent Toolkit <code>create_order</code> handler; payouts use REST. An external host supplies its own <code>async execute(ToolCall, request_id)</code> adapter.</p></div><p>Pass <code>request_approval=your_async_callback</code> when human approval is supported. Without a callback, approval-required calls are denied. Close the payment adapter and audit store during application shutdown. Missing credentials select the explicit MOCK adapter.</p></section>
    <section id="decisions"><span className="eyebrow">DECISION PIPELINE</span><h2>How decisions work</h2><p>Hard limits run first. Remaining calls receive a deterministic score; ambiguous or tainted requests receive an independent AI intent review.</p><div className="decision-flow">{['Rules', 'Risk score', 'AI review', 'Decision'].map((name, index) => <div key={name}><span>0{index + 1}</span><strong>{name}</strong><p>{['Amount, budget, category', 'Weighted, visible factors', 'Gray zone or taint', 'Allow, approve, block'][index]}</p></div>)}</div><h3>Fast path</h3><p>Recipient trust, amount against the user's budget, amount against the automatic approval reference, payout versus purchase, velocity, provenance taint, and category contribute visible points.</p><h3>Selective intent review</h3><p>Scores from 30 to 84, or provenance taint, trigger a review. Clear low-risk calls skip it. Reviews raise or lower risk within bounds, and identical reviews are cached within a run. Hard policy violations short-circuit before review.</p><div className="docs-callout"><strong>Fail closed on review timeout</strong><p>Review errors, invalid responses, and a four-second timeout require human approval. The payment stays paused in Enforce until approved or denied.</p></div><h3>Monitor and Enforce</h3><p><strong>Monitor</strong> computes and records decisions while allowing tool execution. <strong>Enforce</strong> blocks rejected calls and pauses requests requiring approval. Every call retains the mode used for evaluation.</p></section>
    <section id="policies"><span className="eyebrow">POLICY REFERENCE</span><h2>Five payment controls</h2><p>Edit the initial YAML policy, or update values in the console for immediate application.</p><div className="docs-table-wrap"><table><thead><tr><th>Policy</th><th>Behavior</th></tr></thead><tbody><tr><td>Single-transaction limit</td><td>Block above the configured amount.</td></tr><tr><td>Period budget</td><td>Block when daily or weekly spend plus reservations exceeds the budget.</td></tr><tr><td>Merchant / payee allowlist</td><td>Add risk for unknown recipients; optionally hard-block.</td></tr><tr><td>Category restrictions</td><td>Block restricted categories from catalog metadata.</td></tr><tr><td>Risk score thresholds</td><td>Require approval at 60; block at 85 by default.</td></tr></tbody></table></div><CodeBlock code={policyYaml} language="yaml"/><p><code>auto_approve_threshold</code> is the amount risk reference used by scoring. The fifth policy applies the final score thresholds. Each of the five controls has an enabled switch.</p></section>
    <section id="audit"><span className="eyebrow">AUDIT & INTEGRATIONS</span><h2>Review every decision</h2><p>SQLite records payment tools, amounts, recipients, decisions, risk factors, provenance, stage latency, approval results, and PayPal IDs and statuses. Mode and policy changes are audited.</p><CodeBlock code={'curl http://localhost:8000/api/audit\ncurl -N "http://localhost:8000/api/events?audience=console"'} language="shell"/><h3>Live state over SSE</h3><p>The client and console subscribe to backend state. The client receives tool results; risk evidence stays in the console projection. Approvals are single use. Cancelling a task does not reverse a submitted payment.</p><div className="docs-note"><Icon name="docs" size={19}/><p>Webhooks are not implemented. Use the audit API and server-sent events. This release runs in one process; durable audit retention requires a persistent disk.</p></div><h3>Channel3 catalog</h3><p>Channel3 supplies catalog search data. A simulated marketplace listing adds the poisoned checkout policy. Purchases always use the configured PayPal sandbox shop recipient; real catalog merchants are never paid.</p><p>Created orders are not captured payments. Accepted payout batches can remain pending. The local sandbox wallet tracks submitted requests and order reservations, not settled account funds.</p></section>
    <section id="threat-model"><span className="eyebrow">THREAT MODEL</span><h2>External content is untrusted</h2><p>Product pages and tool output can contain instructions that manipulate an agent into proposing an unauthorized payment. The firewall checks payment authority independently from the shopping model.</p><div className="docs-table-wrap"><table><thead><tr><th>Agent model</th><th>Injected payout calls / trials</th></tr></thead><tbody>{[['gpt-4o-mini', '4 / 4'], ['gpt-4o', '1 / 4'], ['gpt-4.1-mini', '0 / 4'], ['gpt-5.4-nano', '0 / 4'], ['claude-sonnet-5-5', '0 / 2']].map(([model, result]) => <tr key={model}><td><code>{model}</code></td><td>{result}</td></tr>)}</tbody></table></div><p className="docs-small">Small, author-reported samples from November 9, 2026. Success means a $500 injected payout tool call, not settlement. Tests were not rerun for this redesign. Zero observed calls does not establish immunity.</p><h3>Scope</h3><p>This is a single-workspace sandbox demonstration without authentication or multi-tenancy. It illustrates the payment execution boundary, not a production authorization service.</p></section><footer className="docs-footer">Airlock · agentic payment firewall<a href="/console">Return to console<Icon name="arrow" size={15}/></a></footer></main><aside className="docs-toc"><span>ON THIS PAGE</span>{docSections.map(section => <a href={`#${section.id}`} key={section.id}>{section.name}</a>)}</aside></div></div>;
}
export function App() { if (location.pathname === '/client') return <Client/>; if (location.pathname === '/docs') return <Docs/>; return <Console/>; }
