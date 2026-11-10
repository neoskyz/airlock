import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ApiError, request } from './api';
import { ClientActivity } from './ClientActivity';
import { VoltStore } from './VoltStore';
import { EventDetail } from './EventDetail';
import { FlaggedEvents } from './FlaggedEvents';
import { ConsoleLink, eventMoney, eventPath, navigateConsole, policyParameterPath } from './consoleNavigation';
import { conflictRun, displayText, isRunActive, isRunBlocking, reconcileRuns, RUN_UNAVAILABLE } from './runUpdates';
import type { Approval, AuditRow, Explainable, Mode, Run } from './types';
import { useLive } from './useLive';
import { Icon, Badge, Connection, ErrorBanner, type IconName } from './ui';
import { Policies } from './Policies';
import { Docs } from './Docs';
import { AuditLogBoundary } from './AuditLogBoundary';
import { AIReviewSummary, DecisionLatency, DecisionMetrics, VelocityParameterLinks } from './DecisionEvidence';
import { decisionTitle, reasonNames } from './decisionPresentation';
const AuditLog = lazy(() => import('./AuditLog'));
const purchase = 'Buy me the cheapest 65W USB-C charger under $40';
const headphones = 'Buy me noise-cancelling headphones under $300';
const money = eventMoney;
const time = (value: string) => new Date(value).toLocaleTimeString('en-US', {
  hour12: false
});
const neutral = (text = '') => displayText(text);
function readSelectedRun() {
  try {
    return sessionStorage.getItem('airlock-client-run');
  } catch {
    return null;
  }
}
function rememberSelectedRun(id: string | null) {
  try {
    if (id) sessionStorage.setItem('airlock-client-run', id);else sessionStorage.removeItem('airlock-client-run');
  } catch {/* The workspace remains usable without browser storage. */}
}
export function Client() {
  const {
    state,
    ready,
    connection,
    error: liveError
  } = useLive('client');
  const {
    config,
    runs: liveRuns,
    wallet
  } = state;
  const [input, setInput] = useState(purchase);
  const [selected, setSelected] = useState(() => readSelectedRun());
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [responseRun, setResponseRun] = useState<Run | null>(null);
  const [conflict, setConflict] = useState('');
  const actionInFlight = useRef(false);
  const runs = reconcileRuns(liveRuns, responseRun);
  useEffect(() => {
    if (!responseRun || responseRun.blocking) return;
    const remote = liveRuns.find(item => item.id === responseRun.id);
    if (remote && (remote.revision ?? -1) >= (responseRun.revision ?? 0)) setResponseRun(null);
  }, [liveRuns, responseRun]);
  useEffect(() => {
    document.title = 'Agentic Payment Client';
  }, []);
  const runningRun = runs.find(isRunBlocking);
  const run = runningRun || runs.find(item => item.id === selected) || runs.at(-1);
  const active = busy || Boolean(runningRun);
  useEffect(() => {
    if (!selected || connection !== 'live' || liveRuns.some(item => item.id === selected)) return;
    let stale = false;
    void request<Run>(`/api/runs/${selected}?audience=client`, {
      signal: AbortSignal.timeout(10000)
    }).then(value => {
      if (!stale) setResponseRun(current => current?.id === value.id && (current.blocking || (current.revision ?? 0) > (value.revision ?? 0)) ? current : value);
    }).catch(err => {
      if (!stale && err instanceof ApiError && err.status === 404) {
        rememberSelectedRun(null);
        setSelected(null);
        setResponseRun(null);
        setError(RUN_UNAVAILABLE);
      }
    });
    return () => {
      stale = true;
    };
  }, [selected, liveRuns, connection]);
  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (active) {
      if (runningRun?.status === 'awaiting_approval') setConflict('This task is waiting for approval. Review it in Airlock or stop the task before starting another.');
      return;
    }
    if (!input.trim() || actionInFlight.current || connection !== 'live') return;
    actionInFlight.current = true;
    setBusy(true);
    setError('');
    setConflict('');
    try {
      const result = await request<{ id: string; run?: Run }>('/api/runs', {
        method: 'POST',
        signal: AbortSignal.timeout(15000),
        body: JSON.stringify({
          message: input.trim()
        })
      });
      setSelected(result.id);
      rememberSelectedRun(result.id);
      setResponseRun(result.run || { id: result.id, status: 'running', revision: 0, messages: [{ role: 'user', content: input.trim() }], events: [], approvals: [] });
    } catch (err) {
      const previous = conflictRun(err);
      if (previous) {
        setResponseRun(previous);
        setSelected(previous.id);
        rememberSelectedRun(previous.id);
        setConflict(previous.status === 'awaiting_approval' ? 'This task is waiting for approval. Review it in Airlock or stop the task before starting another.' : (err as Error).message);
      } else setError((err as Error).message);
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  }
  async function stop() {
    if (!runningRun || busy || actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(true);
    setError('');
    setConflict('');
    try {
      const stopped = await request<Run>(`/api/runs/${runningRun.id}/cancel?audience=client`, {
        method: 'POST',
        signal: AbortSignal.timeout(10000)
      });
      setResponseRun(stopped);
      setSelected(stopped.id);
      rememberSelectedRun(stopped.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setResponseRun(null);
        setSelected(null);
        rememberSelectedRun(null);
      }
      setError(err instanceof ApiError && err.status === 404 ? RUN_UNAVAILABLE : (err as Error).message);
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  }
  return <div className="client-app">
    <header className="client-header">
      <a href="/client" className="client-brand">
        <span className="client-symbol">
          <Icon name="bag" size={23} />
        </span>
        <span>Agentic Payment Client<small>Agent workspace</small></span>
      </a>
      <div className="client-header-right">
        <Connection state={connection} />
        <div className="wallet">
          <span className="wallet-icon">
            <Icon name="wallet" size={22} />
          </span>
          <div>
            <span>PayPal wallet balance</span>
            <strong>
              {!ready ? '—' : money(wallet.balance)}
            </strong>
            <small>
              {wallet.label || 'Sandbox · local balance'}
              {config.paypal_mode === 'MOCK' ? ' · MOCK' : ''}
            </small>
          </div>
        </div>
      </div>
    </header>
    <main className="client-main">
      <div className="client-intro">
        <span className="eyebrow">PAYMENTS WORKSPACE</span>
        <h1>What would you like to do?</h1>
        <p>Find products and send payments through your agent.</p>
      </div>
      <ErrorBanner message={error || liveError} onClose={error ? () => setError('') : undefined} />
      <form className="task-composer" onSubmit={start}>
        <label htmlFor="task">Task</label>
        <div className="task-input-row">
          <input id="task" value={input} onChange={event => setInput(event.target.value)} placeholder="Describe a purchase or payment…" disabled={active} maxLength={4000} />
          <button className="button primary" disabled={active || !input.trim() || connection !== 'live'}>Start<Icon name="arrow" size={18} /></button>
          {active && runningRun?.status !== 'awaiting_approval' && <button type="button" className="button stop" onClick={() => void stop()} disabled={busy || !runningRun}><Icon name="stop" size={17} />Stop</button>}
        </div>
        {runningRun && <span className="task-running-note">{runningRun.status === 'awaiting_approval' ? 'Waiting for approval. The agent will continue once approved.' : runningRun.blocking ? 'This task is still finishing on the server. Stop it before starting another task.' : runningRun.status === 'cancelling' ? 'The task is stopping. Wait for it to finish, or retry Stop.' : 'The agent is working on your task.'}</span>}
        {conflict && runningRun?.status === 'awaiting_approval' && <p className="task-conflict-note" role="status">{conflict}</p>}
      </form>
      <div className="prompt-examples">
        <span>Examples</span>
        {[purchase, headphones].map(prompt => <button key={prompt} onClick={() => setInput(prompt)} disabled={active}>
          {prompt}
          <span>↗</span>
        </button>)}
      </div>
      <section className="activity-section">
        <div className="section-heading">
          <div>
            <h2>Activity</h2>
            <span>
              {run ? `Task ${run.id.slice(0, 8)}` : 'Your agent’s work will appear here'}
            </span>
          </div>
          {(run?.agent_mode === 'compromised' || !run && config.demo_mode) && <Badge>Scripted agent</Badge>}
          {run && <Badge tone={isRunBlocking(run) ? 'blue' : ''}>
            {run.status.replaceAll('_', ' ')}
          </Badge>}
        </div>
        {!ready ? <div className="activity-empty" role="status">
          <h3>Connecting to your workspace</h3>
          <p>Waiting for the latest tasks and payment status.</p>
        </div> : run ? <>
          <div className="request-summary">
            <span>You</span>
            <p>
              {run.messages?.find(item => item.role === 'user')?.content || input}
            </p>
          </div>
          <ClientActivity run={run} onStop={() => void stop()} stopDisabled={busy || !runningRun} />
        </> : <div className="activity-empty">
          <div className="empty-circles">
            <Icon name="search" />
            <span />
            <Icon name="bag" />
            <span />
            <Icon name="check" />
          </div>
          <h3>Ready for a task</h3>
          <p>Catalog search, checkout, and payment results appear as they happen.</p>
        </div>}
      </section>
      <footer className="client-footer">
        <span>Deployed on Render · Audit log powered by AG Grid · Product data from Channel3</span>
        <a href="/console" target="_blank" rel="noreferrer">Open security console<Icon name="external" size={13} /></a>
      </footer>
    </main>
  </div>;
}
function DecisionBadge({
  row
}: {
  row: AuditRow;
}) {
  const monitor = row.mode === 'Monitor' || !row.firewall_enabled;
  const would = monitor && row.decision !== 'ALLOW';
  return <Badge tone={would ? 'monitor' : row.decision === 'BLOCK' ? 'red' : row.decision === 'REQUIRE_APPROVAL' ? 'amber' : 'green'}>
    {row.display_decision || (would ? row.decision === 'BLOCK' ? 'Would have blocked' : 'Would require approval' : row.decision === 'REQUIRE_APPROVAL' ? 'Approval required' : row.decision === 'ALLOW' ? 'Allowed' : 'Blocked')}
  </Badge>;
}
function RiskScore({
  value
}: {
  value?: number | null;
}) {
  return <div className={`risk-score ${(value ?? 0) >= 85 ? 'high' : (value ?? 0) >= 60 ? 'medium' : ''}`}>
    <strong>
      {value ?? '—'}
    </strong>
    <span>/ 100</span>
  </div>;
}
function Factors({
  item
}: {
  item: Explainable;
}) {
  return <div className="factor-list">
    {item.factors?.map((factor, index) => <div className="factor" key={`${factor.name}-${index}`}>
      <div>
        <span>
          {factor.name.replaceAll('_', ' ')}
        </span>
        <p><ConsoleLink href={policyParameterPath(factor.parameter, factor.policy_id)} title="Configure this risk parameter">{neutral(factor.reason)}</ConsoleLink><VelocityParameterLinks code={factor.code} /></p>
      </div>
      <strong className={factor.points > 0 ? 'positive' : ''}>
        {factor.points > 0 ? '+' : ''}
        {factor.points}
      </strong>
    </div>)}
  </div>;
}
function ExecutionStatus({
  row
}: {
  row: AuditRow;
}) {
  const accepted = row.status === 'succeeded' && Boolean(row.paypal_id);
  const label = accepted ? row.tool === 'create_order' ? 'PayPal order created' : 'PayPal payout submitted' : row.status === 'unknown' ? 'Provider outcome unknown · verify sandbox' : row.status === 'executing' ? 'Submitting to PayPal…' : ['blocked', 'denied', 'cancelled'].includes(row.status) ? 'No PayPal call made' : row.status === 'awaiting_approval' ? 'Awaiting approval · no PayPal call made' : row.status.replaceAll('_', ' ');
  return <div className={`feed-execution ${accepted ? 'accepted' : ''}`}>
    <span>
      {label}
    </span>
    {accepted && <>
      <code>
        {row.paypal_id}
      </code>
      <Badge>
        {row.paypal_status || 'ACCEPTED'}
      </Badge>
      {row.mock && <Badge tone="amber">MOCK</Badge>}
    </>}
  </div>;
}
export function FeedCard({ row }: { row: AuditRow }) {
  return <article className={`feed-card ${row.decision.toLowerCase()} ${row.mode === 'Monitor' ? 'monitor' : ''}`}>
    <div className="feed-card-summary">
      <span className="feed-tool-icon">
        <Icon name={row.tool === 'create_order' ? 'bag' : 'arrow'} size={19} />
      </span>
      <div className="feed-tool">
        <code>
          {row.tool}
        </code>
        <span>
          {time(row.timestamp)}
        </span>
      </div>
      <div className="feed-payment">
        <strong>
          {money(row.amount, row.currency)}
        </strong>
        <span>
          {row.payee}
        </span>
      </div>
      <DecisionBadge row={row} />
      <div className="feed-risk">
        <span>Risk score</span>
        <RiskScore value={row.risk_score} />
      </div>
    </div>
    <div className="feed-card-context">
      <strong className="decision-title">{decisionTitle(row)}</strong>
      <div className="risk-chips">{(row.factors || []).filter(factor => factor.points > 0).map((factor, index) => <ConsoleLink key={`${factor.code || factor.name}-${index}`} className="risk-chip" href={policyParameterPath(factor.parameter, factor.policy_id)} title={factor.reason}>{reasonNames[factor.code || ''] || factor.name} +{factor.points}</ConsoleLink>)}</div>
      {row.suspicious_excerpt && <div className="provenance-evidence"><span className="evidence-origin">{row.source || 'Untrusted product page'}</span><mark>{row.suspicious_excerpt}</mark></div>}
      <AIReviewSummary row={row} />
      <DecisionLatency row={row} />
      <ExecutionStatus row={row} />
      <ConsoleLink className="event-link" href={eventPath(row.id)}>View event details →</ConsoleLink>
    </div>
  </article>;
}
function ApprovalInbox({
  approvals,
  rows,
  busy,
  onAnswer,
  selected
}: {
  approvals: Approval[];
  rows: AuditRow[];
  busy: string;
  onAnswer: (id: string, approved: boolean) => void;
  selected: string;
}) {
  useEffect(() => {
    if (selected) document.getElementById(`approval-${selected}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center'
    });
  }, [selected, approvals.length]);
  if (!approvals.length) return <div className="empty-panel">
    <span className="empty-symbol">
      <Icon name="approval" size={27} />
    </span>
    <h3>No pending approvals</h3>
    <p>Payment requests that need human review will appear here.</p>
  </div>;
  return <div className="approval-list">
    {approvals.map(approval => {
      const row = rows.find(item => item.id === approval.id);
      const explanation = {
        ...row,
        ...approval
      };
      return <section id={`approval-${approval.id}`} key={approval.id} className={`approval-card ${selected === approval.id ? 'highlighted' : ''}`}>
      <div className="approval-head">
        <Badge tone="amber">Pending approval</Badge>
        <span>
          {approval.tool}
        </span>
      </div>
      <div className="approval-main">
        <div>
          <h3>
            {money(approval.amount)}
          </h3>
          <p>to <strong>
              {approval.payee}
            </strong></p>
        </div>
        <div>
          <span className="muted-label">Risk score</span>
          <RiskScore value={explanation.risk_score} />
        </div>
      </div>
      <div className="approval-user-context">
        <span>User request</span>
        <p>
          {approval.user_request || approval.provenance?.find(source => source.origin === 'user')?.text || row?.provenance?.find(source => source.origin === 'user')?.text || 'See the originating task for authorization.'}
        </p>
      </div>
      <p className="approval-reason">
        {neutral(approval.reason)}
      </p>
      <div className="approval-breakdown">
        <Factors item={explanation} />
      </div>
      {row?.suspicious_excerpt && <div className="provenance-evidence">
        <span className="evidence-origin">
          {row.source}
        </span>
        <mark>
          {row.suspicious_excerpt}
        </mark>
      </div>}
      <div className="approval-footer">
        <span>Approval permits one execution after a final policy check.</span>
        <div>
          <button className="button secondary" disabled={Boolean(busy)} onClick={() => onAnswer(approval.id, false)}>Deny</button>
          <button className="button primary" disabled={Boolean(busy)} onClick={() => onAnswer(approval.id, true)}>
            <Icon name="check" size={17} />
            {busy === approval.id ? 'Submitting…' : 'Approve payment'}
          </button>
        </div>
      </div>
    </section>;
    })}
  </div>;
}
const navItems: {
  key: string;
  name: string;
  icon: IconName;
  description: string;
}[] = [{
  key: 'feed',
  name: 'Live feed',
  icon: 'feed',
  description: 'Payment tool calls, risk assessments, and execution results.'
}, {
  key: 'approvals',
  name: 'Approvals',
  icon: 'approval',
  description: 'Review payment requests before they reach PayPal.'
}, {
  key: 'policies',
  name: 'Policies',
  icon: 'policy',
  description: 'Five controls evaluated for every payment tool call.'
}, {
  key: 'flagged',
  name: 'Flagged',
  icon: 'shield',
  description: 'Blocked and approval-required calls, including Monitor observations and approval outcomes.'
}, {
  key: 'audit',
  name: 'Audit log',
  icon: 'audit',
  description: 'Search decisions, payment execution, and configuration changes.'
}];
export function Console() {
  const {
    state,
    ready,
    connection,
    error: liveError
  } = useLive('console');
  const {
    config,
    runs,
    audit
  } = state;
  const [route, setRoute] = useState(() => `${location.pathname}${location.hash}`);
  const eventId = location.pathname.startsWith('/console/events/') ? location.pathname.slice('/console/events/'.length) : '';
  const routeTab = route.startsWith('/console/flagged') ? 'flagged' : route.includes('#') ? route.split('#')[1] : 'feed';
  const tab = eventId ? 'event' : navItems.some(item => item.key === routeTab) ? routeTab : 'feed';
  const auditRevision = JSON.stringify(audit.rows);
  const [error, setError] = useState('');
  const [modeBusy, setModeBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetMessage, setResetMessage] = useState('');
  const [answering, setAnswering] = useState('');
  const [selectedApproval, setSelectedApproval] = useState(() => new URLSearchParams(location.search).get('approval') || '');
  const [dismissed, setDismissed] = useState<string[]>([]);
  const approvals = runs.flatMap(run => (run.approvals || []).filter((approval): approval is Approval => 'tool' in approval).map(approval => ({
    ...approval,
    user_request: approval.user_request || run.messages.find(message => message.role === 'user')?.content
  }))).filter(approval => ['pending', 'awaiting_approval'].includes(approval.status));
  const feedRows = audit.rows.filter(row => ['create_order', 'create_payout'].includes(row.tool));
  const pendingNotification = approvals.find(item => !dismissed.includes(item.id));
  const notificationRow = audit.rows.find(row => row.id === pendingNotification?.id);
  const notificationReasons = [...(pendingNotification?.factors || notificationRow?.factors || [])].filter(factor => factor.points > 0).sort((a, b) => b.points - a.points).slice(0, 3);
  const heading = tab === 'event' ? {
    name: 'Event details',
    description: 'Recorded call, policy decisions, evidence, and payment execution.'
  } : navItems.find(item => item.key === tab) || navItems[0];
  const canReset = connection === 'live' && !runs.some(run => isRunActive(run.status)) && !answering;
  useEffect(() => {
    if (!runs.length && !audit.rows.length) {
      if (!new URLSearchParams(location.search).has('approval')) setSelectedApproval('');
      setDismissed([]);
    }
  }, [runs.length, audit.rows.length]);
  function navigate(key: string) {
    navigateConsole(key === 'flagged' ? '/console/flagged' : key === 'feed' ? '/console' : `/console#${key}`);
  }
  useEffect(() => {
    document.title = 'Airlock Console';
  }, []);
  useEffect(() => {
    const handler = () => setRoute(`${location.pathname}${location.hash}`);
    addEventListener('hashchange', handler);
    addEventListener('popstate', handler);
    addEventListener('airlock:navigate', handler);
    return () => {
      removeEventListener('hashchange', handler);
      removeEventListener('popstate', handler);
      removeEventListener('airlock:navigate', handler);
    };
  }, []);
  useEffect(() => {
    window.scrollTo({
      top: 0
    });
  }, [route]);
  async function resetSandbox() {
    if (!canReset || resetBusy) return;
    if (!window.confirm('Reset sandbox data? This clears local audit history, period budget usage, wallet ledger, and tasks. Submitted PayPal orders and payouts are not reversed or deleted.')) return;
    setResetBusy(true);
    setResetMessage('');
    setError('');
    try {
      await request('/api/sandbox/reset', {
        method: 'POST',
        body: JSON.stringify({
          confirmed: true
        }),
        signal: AbortSignal.timeout(15000)
      });
      setSelectedApproval('');
      setDismissed([]);
      setResetMessage('Sandbox data reset. Local budget usage and wallet ledger are clear.');
    } catch (err) {
      setError(`Sandbox reset failed: ${(err as Error).message}`);
    } finally {
      setResetBusy(false);
    }
  }
  async function setMode(mode: Mode) {
    if (mode === config.mode || modeBusy) return;
    setModeBusy(true);
    setError('');
    try {
      await request('/api/mode', {
        method: 'PATCH',
        body: JSON.stringify({
          mode
        })
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setModeBusy(false);
    }
  }
  async function answer(id: string, approved: boolean) {
    if (answering || connection !== 'live') return;
    setAnswering(id);
    setError('');
    try {
      await request(`/api/approvals/${id}`, {
        method: 'POST',
        body: JSON.stringify({
          approved
        })
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAnswering('');
    }
  }
  return <div className="console-app">
    <aside className="console-sidebar">
      <a className="airlock-brand" href="/console">
        <span className="brand-mark">
          <Icon name="shield" size={25} />
        </span>
        <span>Airlock<small>agentic payment firewall</small></span>
      </a>
      <div className="workspace-label">
        <span className="workspace-avatar">S</span>
        <div>Sandbox workspace<small>Payment security</small></div>
        <Badge>SBX</Badge>
      </div>
      <span className="nav-label">WORKSPACE</span>
      <nav>
        {navItems.map(item => <button className={tab === item.key ? 'active' : ''} aria-current={tab === item.key ? 'page' : undefined} key={item.key} onClick={() => navigate(item.key)}>
          <Icon name={item.icon} />
          {item.name}
          {item.key === 'approvals' && approvals.length > 0 && <span className="nav-count">
            {approvals.length}
          </span>}
        </button>)}
        <div className="nav-divider" />
        <a href="/docs"><Icon name="docs" />Docs<Icon name="external" size={14} /></a>
      </nav>
      <div className="sidebar-bottom">
        <a href="/client" target="_blank" rel="noreferrer"><Icon name="external" size={17} />Open payment client</a>
        <div className="sidebar-version"><i />Sandbox environment<span>v{config.version || '0.2.0'}</span></div>
      </div>
    </aside>
    <div className="console-workspace">
      <header className="console-topbar">
        <div className="breadcrumb">Workspace<span>/</span><strong>
            {heading.name}
          </strong></div>
        <div className="topbar-actions">
          <Connection state={connection} />
          <span className="topbar-divider" />
          <span className="mode-label">Mode</span>
          <div className={`mode-switch ${config.mode?.toLowerCase()}`} role="group" aria-label="Firewall mode">
            {(['Monitor', 'Enforce'] as const).map(mode => <button key={mode} className={config.mode === mode ? 'active' : ''} aria-pressed={config.mode === mode} disabled={modeBusy || connection !== 'live'} onClick={() => void setMode(mode)}>{mode === 'Enforce' && <Icon name="shield" size={14} />} {mode}</button>)}
          </div>
        </div>
      </header>
      <main className="console-main">
        <div className="page-heading">
          <div>
            <span className="eyebrow">PAYMENT SECURITY</span>
            <h1>
              {heading.name}
            </h1>
            <p>
              {heading.description}
            </p>
          </div>
          <Badge tone={config.paypal_mode === 'MOCK' ? 'amber' : 'blue'}>PayPal sandbox{config.paypal_mode === 'MOCK' ? ' · MOCK' : ''}</Badge>
        </div>
        <ErrorBanner message={error || liveError} onClose={error ? () => setError('') : undefined} />
        {resetMessage && <div className="reset-success" role="status">
          <Icon name="check" size={17} />
          <span>
            {resetMessage}
          </span>
          <button aria-label="Dismiss reset confirmation" onClick={() => setResetMessage('')}>
            <Icon name="close" size={15} />
          </button>
        </div>}
        {connection === 'reconnecting' && <div className="connection-notice">Live sync disconnected. Reconnecting automatically; check the audit log before repeating a payment.</div>}
        {!ready && !['flagged', 'event'].includes(tab) && <div className="empty-panel" role="status">
          <h3>Loading workspace</h3>
          <p>Waiting for payment calls, policies, and approval status.</p>
        </div>}
        {ready && tab === 'feed' && <>
          <div className={`mode-notice ${config.mode?.toLowerCase()}`}>
            <Icon name={config.mode === 'Enforce' ? 'shield' : 'feed'} size={19} />
            <div>
              <strong>
                {config.mode === 'Enforce' ? 'Enforce mode' : 'Monitor mode'}
              </strong>
              <span>
                {config.mode === 'Enforce' ? 'Policy decisions are enforced before a payment can execute.' : 'All calls execute. Policy decisions are computed and recorded for review.'}
              </span>
            </div>
            <span className="notice-label">
              {config.mode === 'Enforce' ? 'Protection active' : 'Observe only'}
            </span>
          </div>
          <div className="stats-grid">
            <div className="stat-card">
              <span>PAYMENT CALLS</span>
              <strong>
                {feedRows.length}
              </strong>
              <small>Across all client tasks</small>
            </div>
            <button type="button" className="stat-card risk-stat" onClick={() => navigate('flagged')}>
              <span>Flagged by policies</span>
              <strong>
                {feedRows.filter(row => ['BLOCK', 'REQUIRE_APPROVAL'].includes(row.computed_decision || row.decision)).length}
                <small>calls</small>
              </strong>
              <small>{feedRows.filter(row => row.mode === 'Monitor' && (row.computed_decision || row.decision) === 'BLOCK').length} would have been blocked in Monitor</small>
            </button>
            <div className="stat-card">
              <span>AWAITING APPROVAL</span>
              <strong>
                {approvals.length}
                <i className={approvals.length ? 'stat-indicator' : 'stat-indicator quiet'} />
              </strong>
              <small>Human review before execution</small>
            </div>
          </div>
          <DecisionMetrics stats={audit.stats} />
          <div className="feed-section-heading">
            <h2>Recent tool calls<span>
                {feedRows.length}
              </span></h2>
            <span>
              {config.judge_model ? `Intent review · ${neutral(config.judge_model)}` : 'Hybrid risk scoring'}
            </span>
          </div>
          <div className="feed-list">
            {feedRows.length ? feedRows.map(row => <FeedCard row={row} key={row.id} />) : <div className="empty-panel">
              <span className="empty-symbol">
                <Icon name="feed" size={29} />
              </span>
              <h3>Listening for payment calls</h3>
              <p>Open the payment client in another tab and start a task.</p>
              <a className="button secondary" href="/client" target="_blank" rel="noreferrer">Open payment client<Icon name="external" size={15} /></a>
            </div>}
          </div>
        </>}
        {ready && tab === 'approvals' && <>
          <div className="approval-context-summary">{approvals.length} pending request{approvals.length === 1 ? '' : 's'}<span>Approve only payments you recognize. Denied requests never reach PayPal.</span></div>
          <ApprovalInbox approvals={approvals} rows={audit.rows} busy={connection === 'live' ? answering : 'offline'} onAnswer={(id, approved) => void answer(id, approved)} selected={selectedApproval} />
        </>}
        {ready && tab === 'policies' && <Policies config={config} online={connection === 'live'} onError={setError} onReset={() => void resetSandbox()} resetBusy={resetBusy} canReset={Boolean(canReset)} />}
        {ready && tab === 'audit' && <AuditLogBoundary>
          <Suspense fallback={<div className="empty-panel" role="status">Loading audit table…</div>}>
            <AuditLog rows={audit.rows} />
          </Suspense>
        </AuditLogBoundary>}
        {tab === 'flagged' && <FlaggedEvents revision={auditRevision} />}
        {tab === 'event' && <EventDetail key={eventId} id={eventId} revision={auditRevision} />}
      </main>
      <footer className="console-footer">Deployed on Render · Audit log powered by AG Grid · Product data from Channel3</footer>
    </div>
    {pendingNotification && <div className="push-notification" role="status">
      <div className="push-app">
        <span className="push-app-icon">
          <Icon name="shield" size={16} />
        </span>
        <strong>AIRLOCK</strong>
        <span>now</span>
        <button aria-label="Dismiss notification" onClick={() => setDismissed(current => [...current, pendingNotification.id])}>
          <Icon name="close" size={15} />
        </button>
      </div>
      <button className="push-content" onClick={() => {
        navigate('approvals');
        setSelectedApproval(pendingNotification.id);
        setDismissed(current => [...current, pendingNotification.id]);
      }}>
        <h3>Approve {money(pendingNotification.amount)} to {pendingNotification.payee}?</h3>
        <div className="push-risk">
          <span>Risk score <strong>
              {pendingNotification.risk_score ?? notificationRow?.risk_score ?? '—'}
            </strong></span>
          <span>Human approval required</span>
        </div>
        {notificationReasons.length ? <ul>
          {notificationReasons.map(reason => <li key={reason.name}>
            {neutral(reason.reason || reason.name)}
          </li>)}
        </ul> : <p>
          {neutral(pendingNotification.reason)}
        </p>}
        <span className="push-action">Tap to review payment<Icon name="arrow" size={14} /></span>
      </button>
    </div>}
  </div>;
}
export function App() {
  if (location.pathname === '/store/volt-65w') return <VoltStore />;
  if (location.pathname === '/client') return <Client />;
  if (location.pathname === '/docs') return <Docs />;
  return <Console />;
}
