import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';
import { request } from './api';
import type { AgentMode, Approval, Audit, AuditRow, Config, Run, TimelineEvent } from './types';

ModuleRegistry.registerModules([AllCommunityModule]);
const money = (value: string | number | undefined) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0));
const purchase = 'Buy me a USB-C charger under $40';
const contractor = 'Also pay my contractor $120 invoice';
const agentModeLabel = (mode: AgentMode) => mode === 'compromised' ? 'Simulated compromised agent' : 'Live Claude agent';
const gridTheme = themeQuartz.withParams({
  backgroundColor: '#13191d', foregroundColor: '#d8e2e7', headerBackgroundColor: '#1b2329',
  headerTextColor: '#95a6af', borderColor: '#29343b', accentColor: '#a3efbe',
  fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif', fontSize: 12,
  rowHoverColor: '#ffffff08', wrapperBorderRadius: 10,
});
function Shield({ small = false }: { small?: boolean }) {
  return <svg className={small ? 'shield small' : 'shield'} viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m12 2 8 3.5V12c0 4.5-8 10-8 10S4 16.5 4 12V5.5L12 2Z" stroke="currentColor" strokeWidth="1.6"/><path d="m8.5 11.5 2.5 2.5 4.5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}
function DecisionPill({ decision }: { decision?: string }) {
  return <span className={`decision-pill ${(decision || '').toLowerCase()}`}>{decision === 'REQUIRE_APPROVAL' ? 'APPROVAL REQUIRED' : decision || 'TOOL CALL'}</span>;
}
function ExecutionState({ event }: { event: TimelineEvent }) {
  if (!event.paypal_id && !event.status) return null;
  return <div className="execution-state"><span className={event.status === 'failed' ? 'failed-text' : 'muted'}>{event.status === 'succeeded' ? 'Request accepted' : event.status?.replaceAll('_', ' ')}</span>{event.paypal_status && <span className="provider-state">PayPal: {event.paypal_status}</span>}{event.paypal_id && <code>{event.paypal_id}</code>}{event.mock && <span className="tiny-mock">MOCK</span>}</div>;
}
function TimelineCard({ event, index }: { event: TimelineEvent; index: number }) {
  return <article className={`timeline-card ${(event.decision || event.type).toLowerCase()}`}>
    <div className="timeline-node">{event.decision === 'BLOCK' ? '×' : event.decision === 'REQUIRE_APPROVAL' ? '!' : event.paypal_id ? '✓' : index + 1}</div>
    <div className="event-topline"><code>{event.tool || (event.type === 'provenance' ? 'provenance.capture' : event.type.replaceAll('_', '.'))}</code><DecisionPill decision={event.decision || (event.type === 'tool_result' ? 'EXECUTED' : undefined)}/></div>
    {(event.amount !== undefined || event.payee) && <div className="event-payment"><strong>{money(event.amount)}</strong><span>→</span><span className="payee">{event.payee}</span></div>}
    {(event.reason || event.message) && <p className="event-reason">{event.reason || event.message}</p>}
    {event.suspicious_excerpt && <div className="taint-evidence"><span>UNTRUSTED TOOL OUTPUT · {event.source || 'product description'}</span><mark>{event.suspicious_excerpt}</mark></div>}
    <ExecutionState event={event}/>
  </article>;
}
function ApprovalCard({ approval, busy, onAnswer }: { approval: Approval; busy: boolean; onAnswer: (id: string, approved: boolean) => void }) {
  return <section className="approval-card" aria-label="Payment approval">
    <div className="approval-eyebrow"><span className="amber-dot"/>HUMAN CHECKPOINT</div>
    <h3>Your approval is required</h3><p>The agent is paused before this payment executes.</p>
    <div className="approval-payment"><strong>{money(approval.amount)}</strong><span>{approval.payee}</span></div>
    <div className="approval-reason">{approval.reason}</div>
    <div className="approval-actions"><button className="approve-button" onClick={() => onAnswer(approval.id, true)} disabled={busy}>✓ Approve payment</button><button className="deny-button" onClick={() => onAnswer(approval.id, false)} disabled={busy}>Deny</button></div>
  </section>;
}
function ProductEvidence({ run }: { run: Run }) {
  const event = run.events.find(item => item.type === 'provenance' && item.products?.length);
  if (!event?.products?.length) return null;
  const excerpt = event.suspicious_excerpt;
  const product = event.products.find(item => excerpt && item.description.includes(excerpt)) || event.products[0];
  const suspicious = excerpt && product.description.includes(excerpt) ? excerpt : '';
  return <div className="product-evidence"><div className="product-source"><span>↳</span><code>search_products</code><span className="untrusted-label">UNTRUSTED SOURCE</span></div><div className="product-heading"><div className="charger-icon">ϟ</div><div><strong>{product.name}</strong><span>Mock store · {product.merchant}</span></div><b>{money(product.price)}</b></div><p>{suspicious ? product.description.replace(suspicious, '').trim() : product.description}</p>{suspicious && <div className="injected-text"><span>INJECTED INSTRUCTION · PRODUCT DESCRIPTION</span><mark>{suspicious}</mark></div>}</div>;
}
function Architecture() {
  return <div className="architecture" aria-label="Architecture: Agent to Firewall to PayPal Agent Toolkit for orders or REST for payouts to Sandbox"><span className="arch-node">AI agent</span><span className="arch-arrow">→</span><span className="arch-node arch-firewall"><Shield small/>Firewall</span><span className="arch-arrow">→</span><span className="arch-node" title="Orders use PayPal Agent Toolkit; payouts use REST">PayPal Agent Toolkit / REST</span><span className="arch-arrow">→</span><span className="arch-node">Sandbox</span><span className="architecture-note">Drop-in for any agent. Open source.</span></div>;
}

export function App() {
  const [tab, setTab] = useState<'demo' | 'audit'>('demo');
  const [config, setConfig] = useState<Config | null>(null);
  const [firewall, setFirewall] = useState(true);
  const [agentMode, setAgentMode] = useState<AgentMode>('compromised');
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState(purchase);
  const [sending, setSending] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [error, setError] = useState('');
  const [audit, setAudit] = useState<Audit>({ rows: [], stats: { blocked_amount: 0, allowed_amount: 0 } });
  const [auditFilter, setAuditFilter] = useState('');
  const [decisionFilter, setDecisionFilter] = useState('ALL');
  const [auditLoading, setAuditLoading] = useState(false);
  const chatBottom = useRef<HTMLDivElement>(null);
  const timelineBottom = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);
  const activeRun = runs.find(run => run.id === activeId);
  const llmDegraded = Boolean(config?.llm_degraded || activeRun?.llm_degraded);
  const displayedMode = activeRun?.agent_mode || (llmDegraded ? 'compromised' : agentMode);
  const judgeMode = llmDegraded ? 'rules' : activeRun?.judge_mode || config?.judge_mode;
  const running = sending || Boolean(activeId && (!activeRun || ['running', 'awaiting_approval'].includes(activeRun.status)));
  const pendingApprovals = activeRun?.approvals.filter(approval => ['pending', 'awaiting_approval'].includes(approval.status.toLowerCase())) || [];
  const allEvents = runs.flatMap(run => run.events.filter(event => event.type !== 'message').map(event => ({ ...event, runId: run.id })));
  const refreshAudit = useCallback(async () => {
    setAuditLoading(true);
    try { setAudit(await request<Audit>('/api/audit')); }
    catch (err) { setError(`Unable to load audit: ${(err as Error).message}`); }
    finally { setAuditLoading(false); }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let initialized = false;
    const refreshConfig = () => request<Config>('/api/config', { signal: controller.signal }).then(value => {
      const firstLoad = !initialized;
      setConfig(value);
      setAgentMode(current => firstLoad || !value.live_available ? value.agent_mode : current);
      initialized = true;
    }).catch(err => {
      if (!initialized && err.name !== 'AbortError') setError(`Backend unavailable: ${err.message}. Start the FastAPI server and reload.`);
    });
    void refreshConfig();
    const interval = setInterval(() => { void refreshConfig(); }, 5000);
    return () => { clearInterval(interval); controller.abort(); };
  }, []);
  useEffect(() => { void refreshAudit(); }, [refreshAudit, tab]);
  useEffect(() => {
    if (!activeId) return;
    let stopped = false;
    let timeout: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const run = await request<Run>(`/api/runs/${activeId}`, { signal: controller.signal });
        if (stopped) return;
        setError(current => current.startsWith('Live update failed:') ? '' : current);
        setRuns(current => current.some(item => item.id === run.id) ? current.map(item => item.id === run.id ? run : item) : [...current, run]);
        if (run.status === 'completed' || run.status === 'failed') {
          setActiveId(null);
          void request<Config>('/api/config').then(setConfig).catch(() => {});
          await refreshAudit();
          return;
        }
      } catch (err) {
        if (stopped) return;
        setError(`Live update failed: ${(err as Error).message}. Retrying…`);
      }
      if (!stopped) timeout = setTimeout(poll, 700);
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timeout); };
  }, [activeId, refreshAudit]);
  const messageCount = runs.reduce((count, run) => count + run.messages.length, 0);
  const eventCount = allEvents.length;
  useEffect(() => { chatBottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [messageCount, eventCount, pendingApprovals.length, tab]);
  useEffect(() => { timelineBottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [eventCount, tab]);

  async function startRun(message: string, enabled = firewall) {
    if (!message.trim() || inFlight.current || running || !config) return;
    inFlight.current = true;
    setSending(true); setError(''); setFirewall(enabled); setTab('demo');
    try {
      const { id } = await request<{ id: string }>('/api/runs', { method: 'POST', body: JSON.stringify({ message: message.trim(), firewall_enabled: enabled, agent_mode: agentMode }) });
      setRuns(current => [...current, { id, status: 'running', firewall_enabled: enabled, agent_mode: agentMode, messages: [{ role: 'user', content: message.trim() }], events: [], approvals: [] }]);
      setActiveId(id); setInput('');
    } catch (err) { setError(`Could not start the agent: ${(err as Error).message}`); }
    finally { setSending(false); inFlight.current = false; }
  }
  async function answerApproval(id: string, approved: boolean) {
    if (answering) return;
    setAnswering(true); setError('');
    try {
      await request(`/api/approvals/${id}`, { method: 'POST', body: JSON.stringify({ approved }) });
      setRuns(current => current.map(run => ({ ...run, approvals: run.approvals.map(approval => approval.id === id ? { ...approval, status: approved ? 'approved' : 'denied' } : approval) })));
      void refreshAudit();
    } catch (err) { setError(`Approval failed: ${(err as Error).message}`); }
    finally { setAnswering(false); }
  }
  const columns = useMemo<ColDef<AuditRow>[]>(() => [
    { field: 'timestamp', headerName: 'Time', minWidth: 155, valueFormatter: ({ value }) => value ? new Date(value).toLocaleTimeString('en-US', { hour12: false }) : '', tooltipField: 'timestamp' },
    { field: 'tool', headerName: 'Tool', minWidth: 155 },
    { field: 'amount', headerName: 'Amount', minWidth: 120, filter: 'agNumberColumnFilter', valueGetter: ({ data }) => Number(data?.amount || 0), valueFormatter: ({ value }) => money(value) },
    { field: 'payee', headerName: 'Payee', minWidth: 240 },
    { field: 'decision', headerName: 'Decision', minWidth: 205, cellRenderer: (params: ICellRendererParams<AuditRow>) => <DecisionPill decision={params.value}/> },
    { field: 'reason', headerName: 'Reason', minWidth: 340, flex: 1, tooltipField: 'reason' },
    { field: 'status', headerName: 'Execution', minWidth: 145 },
    { field: 'paypal_status', headerName: 'PayPal state', minWidth: 145 },
    { field: 'paypal_id', headerName: 'PayPal ID', minWidth: 220 },
    { field: 'mock', headerName: 'Provider', minWidth: 125, valueFormatter: ({ value }) => value ? 'MOCK' : 'SANDBOX' },
    { field: 'firewall_enabled', headerName: 'Firewall', minWidth: 120, valueFormatter: ({ value }) => value ? 'ON' : 'OFF' },
  ], []);
  const filteredRows = audit.rows.filter(row => decisionFilter === 'ALL' || row.decision === decisionFilter);
  const totalDecisions = audit.rows.length;
  const mock = config?.paypal_mode?.toLowerCase().includes('mock');

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#" onClick={event => { event.preventDefault(); setTab('demo'); }}><div className="brand-mark"><Shield/></div><span>Air<span className="brand-guard">lock</span><small>PAYMENT FIREWALL</small></span></a><div className="topbar-right"><span className={`environment-badge ${mock ? 'mock' : ''}`}>{config ? (mock ? 'MOCK PAYPAL' : 'PAYPAL SANDBOX') : 'CONNECTING'}</span>{config && <span className="mode-badge">{displayedMode === 'compromised' ? 'SIMULATED AGENT' : 'LIVE CLAUDE'}</span>}{judgeMode && <span className="mode-badge">{judgeMode.toLowerCase().includes('rule') ? 'RULE JUDGE' : 'LLM JUDGE'}</span>}{llmDegraded && <span className="environment-badge mock">DAILY LLM LIMIT · DEGRADED</span>}<div className="header-divider"/><div className="firewall-toggle"><div><b>Firewall</b><small>{firewall ? 'Protecting tool calls' : 'Payments unprotected'}</small></div><button type="button" role="switch" aria-checked={firewall} aria-label="Payment firewall" disabled={running} onClick={() => setFirewall(value => !value)} className={`switch ${firewall ? 'on' : ''}`}><span/></button><span className={firewall ? 'toggle-on' : 'toggle-off'}>{firewall ? 'ON' : 'OFF'}</span></div></div></header>
    <main>
      <div className="page-heading"><div><div className="eyebrow"><span className="live-dot"/>AGENTIC COMMERCE, WITH GUARDRAILS</div><h1>Good intent. <span>Safer payments.</span></h1><p>One poisoned product page should never become a payment instruction.</p></div><div className="hackathon-mark"><span>BUILT FOR</span><b><i>Pay</i>Pal</b><small>AI HACKATHON · 2026</small></div></div>
      <div className="workspace-bar"><nav aria-label="Workspace"><button className={tab === 'demo' ? 'selected' : ''} onClick={() => setTab('demo')}><span>◈</span> Agent workspace</button><button className={tab === 'audit' ? 'selected' : ''} onClick={() => setTab('audit')}><span>▤</span> Audit log <b>{totalDecisions}</b></button></nav><span className="workspace-note">Policy + intent review + human approval</span></div>
      {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}>×</button></div>}
      {llmDegraded && <div className="mock-notice" role="status"><span>LLM LIMIT</span> Today's AI call limit has been reached. New runs use a scripted agent and rule judge until the next UTC day. An active live run stops before its next model call to avoid replaying payments.</div>}
      {mock && <div className="mock-notice"><span>MOCK</span> No PayPal credentials configured. Payments are simulated; IDs are clearly labeled. No money moves.</div>}
      {tab === 'demo' ? <>
        <section className="agent-mode-bar" aria-label="Agent mode">
          <label htmlFor="agent-mode">AGENT MODE<select id="agent-mode" value={agentMode} disabled={running || !config} onChange={event => setAgentMode(event.target.value as AgentMode)} aria-describedby="agent-mode-detail"><option value="compromised">Simulated compromised agent</option><option value="live" disabled={!config?.live_available}>Live Claude agent</option></select></label>
          <p id="agent-mode-detail">{agentMode === 'compromised' ? 'Replays a successful injection to make firewall decisions reproducible.' : 'Claude may resist the injected checkout policy; an attack attempt is not guaranteed.'}<span>Intent judge: {llmDegraded ? 'rules (daily LLM limit reached)' : config?.judge_model || 'connecting…'}{config && !config.live_available && !llmDegraded ? ' · Live mode requires an Anthropic key.' : ''}</span></p>
        </section>
        <section className="scenario-strip" aria-label="Demo scenarios"><span className="scenario-label">TRY A SCENARIO</span><button disabled={running || !config} onClick={() => void startRun(purchase, false)}><span className="scenario-number">01</span>Attack · firewall off <span className="scenario-arrow">↗</span></button><button disabled={running || !config} onClick={() => void startRun(purchase, true)}><span className="scenario-number">02</span>Attack · firewall on <span className="scenario-arrow">↗</span></button><button disabled={running || !config} onClick={() => void startRun(contractor, true)}><span className="scenario-number">03</span>Contractor approval <span className="scenario-arrow">↗</span></button></section>
        <div className="workspace-grid"><section className="chat-panel panel"><div className="panel-header"><div className="panel-title"><span className="agent-avatar">A</span><div><h2>Shopping & payments agent</h2><p>{config ? `${agentModeLabel(displayedMode)}${displayedMode === 'live' ? ` · ${config.agent_model}` : ''}` : 'Connecting to agent'}</p></div></div><span className="panel-kicker">CONVERSATION</span></div>
          <div className="conversation" aria-live="polite" aria-relevant="additions text">
            {!runs.length && <div className="empty-chat"><div className="empty-shield"><Shield/></div><h3>Your agent can act.<br/>You set the boundaries.</h3><p>Ask it to buy a charger. See how the firewall handles an instruction hidden in a product page.</p><div className="empty-capabilities"><span>✓ Intent verification</span><span>✓ Source tracing</span><span>✓ Payment approvals</span></div></div>}
            {runs.map((run, runIndex) => <div className="chat-run" key={run.id}><div className="run-separator"><span>RUN {String(runIndex + 1).padStart(2, '0')}</span><span className="run-agent-mode">{agentModeLabel(run.agent_mode)}</span><span className={run.firewall_enabled ? 'protected-label' : 'unprotected-label'}>{run.firewall_enabled ? '◉ FIREWALL ON' : '○ FIREWALL OFF'}</span></div>{run.messages.map((message, messageIndex) => <div key={`${run.id}-${messageIndex}`} className={`chat-message ${message.role}`}><div className="message-avatar">{message.role === 'user' ? 'U' : 'A'}</div><div><span className="message-author">{message.role === 'user' ? 'You' : 'Agent'}</span><p>{message.content}</p></div></div>)}<ProductEvidence run={run}/>{run.error && <p className="run-error">{run.error}</p>}{run.status === 'running' && <div className="agent-thinking"><span/><span/><span/>Agent is working…</div>}</div>)}
            {pendingApprovals.map(approval => <ApprovalCard key={approval.id} approval={approval} busy={answering} onAnswer={(id, approved) => void answerApproval(id, approved)}/>)}<div ref={chatBottom}/>
          </div>
          <form className="composer" onSubmit={event => { event.preventDefault(); void startRun(input); }}><label className="sr-only" htmlFor="agent-message">Message the agent</label><textarea id="agent-message" placeholder="Ask your agent to make a payment…" rows={2} value={input} disabled={running} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void startRun(input); } }}/><div className="composer-footer"><span>{running ? (pendingApprovals.length ? 'Paused · waiting for your approval' : 'Processing your request…') : 'Enter to send · Shift + Enter for a new line'}</span><button type="submit" aria-label="Send message" disabled={running || !input.trim() || !config}>↑</button></div></form>
        </section>
        <section className="timeline-panel panel"><div className="panel-header"><div><h2>Decision timeline <span className="count-badge">{allEvents.filter(event => event.decision).length}</span></h2><p>Every tool call. Every reason. Every outcome.</p></div><span className={`live-label ${running ? 'is-running' : ''}`}><span/>{running ? 'LIVE' : 'READY'}</span></div><div className="timeline-body" aria-live="polite">{!allEvents.length ? <div className="empty-timeline"><div className="timeline-placeholder"><span/><span/><span/></div><h3>Trust, checked at every step.</h3><p>Run a scenario to trace instructions from their source to the payment decision.</p><div className="layer-list"><span><b>01</b>Deterministic policy</span><span><b>02</b>Intent & provenance review</span><span><b>03</b>Execute, block, or ask you</span></div></div> : <div className="timeline-list">{allEvents.map((event, index) => <TimelineCard key={`${event.runId}-${event.id || index}`} event={event} index={index}/>)}<div ref={timelineBottom}/></div>}</div><div className="timeline-footer"><Shield small/><span>{firewall ? 'Intent review fails closed. Errors block payment.' : 'Firewall bypassed. Tool calls execute without review.'}</span></div></section></div>
        <Architecture/>
      </> : <section className="audit-view"><div className="audit-heading"><div><h2>A clear record of every decision.</h2><p>Persistent SQLite audit trail · filter any column to investigate.</p></div><button className="secondary-button" onClick={() => void refreshAudit()} disabled={auditLoading}>{auditLoading ? 'Refreshing…' : '↻ Refresh'}</button></div><div className="stats-grid"><div className="stat-card blocked-stat"><span>BLOCKED VALUE</span><strong>{money(audit.stats.blocked_amount)}</strong><small>Stopped before reaching PayPal</small></div><div className="stat-card allowed-stat"><span>ALLOWED VALUE</span><strong>{money(audit.stats.allowed_amount)}</strong><small>Executed through the payment adapter</small></div><div className="stat-card"><span>AUDIT RECORDS</span><strong>{totalDecisions.toString().padStart(2, '0')}</strong><small>Decisions with traceable evidence</small></div></div><div className="audit-toolbar"><label><span>⌕</span><input aria-label="Search audit records" placeholder="Search tools, payees, reasons, IDs…" value={auditFilter} onChange={event => setAuditFilter(event.target.value)}/></label><select aria-label="Filter by decision" value={decisionFilter} onChange={event => setDecisionFilter(event.target.value)}><option value="ALL">All decisions</option><option value="ALLOW">Allow</option><option value="BLOCK">Block</option><option value="REQUIRE_APPROVAL">Approval required</option><option value="BYPASS">Bypass</option></select><span className="grid-credit">AG GRID COMMUNITY</span></div><div className="audit-grid"><AgGridReact<AuditRow> theme={gridTheme} rowData={filteredRows} columnDefs={columns} defaultColDef={{ sortable: true, filter: true, resizable: true, floatingFilter: true }} quickFilterText={auditFilter} getRowId={({ data }) => String(data.id)} rowHeight={52} headerHeight={44} pagination paginationPageSize={10} paginationPageSizeSelector={[10, 25, 50]} enableBrowserTooltips rowClassRules={{ 'audit-block': params => params.data?.decision === 'BLOCK', 'audit-allow': params => params.data?.decision === 'ALLOW', 'audit-approval': params => params.data?.decision === 'REQUIRE_APPROVAL', 'audit-bypass': params => params.data?.decision === 'BYPASS' }} overlayNoRowsTemplate="<span>No audit records yet. Run a demo scenario to create your first decision.</span>"/></div><div className="policy-and-architecture"><section className="policy-panel panel"><div className="panel-header"><div><h2>Policy as code</h2><p>Deterministic checks before intent review.</p></div><span className="file-badge">YAML</span></div><pre><code>{config?.policy_yaml || 'Loading firewall policy…'}</code></pre></section><div className="audit-explainer"><Shield/><h3>Permission to act.<br/>A boundary to protect.</h3><p>The firewall reviews the user's intent, the proposed payment, and where each instruction came from. Untrusted product text never grants payment authority.</p><Architecture/></div></div></section>}
      <footer className="page-footer"><span><Shield small/>Airlock</span><span>Powered by PayPal Agent Toolkit</span><span>Sandbox only · MIT licensed</span></footer>
    </main>
  </div>;
}
