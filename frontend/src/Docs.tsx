import { useEffect, useState } from 'react';
import { Icon, Badge } from './ui';
function CodeBlock({
  code,
  language = 'python'
}: {
  code: string;
  language?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setFailed(false);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setFailed(true);
    }
  }
  return <div className="code-block">
    <div>
      <span>
        {language}
      </span>
      <button onClick={() => void copy()}>
        <Icon name="copy" size={14} />
        {copied ? 'Copied' : failed ? 'Select code to copy' : 'Copy'}
      </button>
    </div>
    <pre>
      <code>
        {code}
      </code>
    </pre>
  </div>;
}
const setup = `import os\nfrom firewall import AuditStore, Firewall, Policy, Provenance, ToolCall, build_judge\nfrom airlock.paypal import build_paypal\n\npaypal = build_paypal(os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET"))\ncall = ToolCall(tool="create_order", amount="29.00", payee="shop@chargers.example", description="USB-C charger", category="electronics")\nprovenance = Provenance("Buy me a USB-C charger under $40")\nintent_judge = build_judge(api_key=None)  # Explicit rules fallback`;
const quickstart = `guard = Firewall(Policy.load("policies/default.yaml"), intent_judge, AuditStore("audit.sqlite3"))\nprotected_paypal = guard.wrap_tool(paypal.execute)\nresult = await protected_paypal(call, provenance, mock=paypal.mock)`;
const policyYaml = `currency: USD\nsingle_transaction_limit: 1000\ndaily_limit: 2000\nperiod: weekly\nauto_approve_threshold: 50\nallowed_payees:\n  - shop@chargers.example\nunknown_payee_action: REQUIRE_APPROVAL\nblocked_categories:\n  - gift cards\n  - crypto\n  - gambling\nrisk_approval_threshold: 60\nrisk_block_threshold: 85\nenabled:\n  single_transaction: true\n  period_budget: true\n  allowlist: true\n  categories: true\n  risk_threshold: true\nvelocity_window_seconds: 60\nvelocity_call_threshold: 3\nreview_min_score: 30\nrisk_weights:\n  unknown_payee: 18\n  unknown_merchant: 50\n  new_payee: 20\n  not_requested: 30\n  negated: 50\n  over_budget: 20\n  above_auto_approve_limit: 20\n  payout_tool: 24\n  velocity: 10\n  unknown_merchant_velocity: 4\n  untrusted_source: 40\n  category: 5`;
const docSections = [{
  id: 'quickstart',
  name: 'Quickstart'
}, {
  id: 'decisions',
  name: 'How decisions work'
}, {
  id: 'policies',
  name: 'Policy reference'
}, {
  id: 'audit',
  name: 'Audit & integrations'
}, {
  id: 'threat-model',
  name: 'Threat model'
}];
export function Docs() {
  useEffect(() => {
    document.title = 'Airlock · SDK documentation';
  }, []);
  return <div className="docs-app">
    <header className="docs-header">
      <a className="airlock-brand" href="/console"><span className="brand-mark">
          <Icon name="shield" size={24} />
        </span>Airlock<span className="docs-header-label">Documentation</span></a>
      <div>
        <Badge>Python SDK</Badge>
        <a href="/console">Console<Icon name="external" size={15} /></a>
      </div>
    </header>
    <div className="docs-layout">
      <aside className="docs-sidebar">
        <span className="nav-label">PYTHON SDK</span>
        {docSections.map(section => <a href={`#${section.id}`} key={section.id}>
          {section.name}
        </a>)}
        <div className="docs-sidebar-note">
          <Icon name="shield" size={19} />
          <p>Agent-independent policy and payment protection.</p>
          <span>Python 3.13 · v0.2.0</span>
        </div>
      </aside>
      <main className="docs-content">
        <div className="docs-breadcrumb">Documentation<span>/</span>Python SDK</div>
        <section id="quickstart">
          <span className="eyebrow">QUICKSTART</span>
          <h1>Protect your agent in 3 lines</h1>
          <p className="docs-lead">Wrap PayPal Agent Toolkit execution with a reusable payment firewall. Each call receives a policy decision, an explainable risk score, and an audit record.</p>
          <CodeBlock code={quickstart} />
          <h3>Set up your sandbox adapter</h3>
          <p>Install the project with Python 3.13 and <code>uv sync</code>. Prepare the adapter, reviewed tool call, and user provenance before the three-line wrapper:</p>
          <CodeBlock code={setup} />
          <div className="docs-note">
            <Icon name="docs" size={19} />
            <p><code>build_paypal</code> is this application's sandbox adapter. Its order executor calls the real PayPal Agent Toolkit <code>create_order</code> handler; payouts use REST. An external host supplies its own <code>async execute(ToolCall, request_id)</code> adapter.</p>
          </div>
          <p>Pass <code>request_approval=your_async_callback</code> when human approval is supported. Without a callback, approval-required calls are denied. Missing credentials select the explicit MOCK adapter. This example uses the rules fallback; provide your independent review key to enable AI intent review.</p>
        </section>
        <section id="decisions">
          <span className="eyebrow">DECISION PIPELINE</span>
          <h2>How decisions work</h2>
          <p>Rules make clear low-risk and high-risk decisions immediately. Only gray-zone calls wait for AI intent review. Every decision immediately shows rule factors and original source evidence.</p>
          <div className="decision-flow">
            {['Fast decision', 'Gray-zone review', 'Structured evidence'].map((name, index) => <div key={name}>
              <span>0{index + 1}</span>
              <strong>
                {name}
              </strong>
              <p>
                {['Rules allow or block immediately', 'Only scores 30–84 wait for AI', 'Rule title, factors, source excerpts'][index]}
              </p>
            </div>)}
          </div>
          <h3>Fast path</h3>
          <p>Recipient trust, amount against the user's budget, amount against the auto-approve limit, payout versus purchase, velocity, provenance taint, and category contribute visible points. Scores below 30 allow immediately; scores of 85 or above and hard policy violations block immediately. These decisions make no model call. Taint adds risk points; it does not independently trigger review.</p>
          <h3>Selective intent review</h3>
          <p>Only scores from 30 to 84 trigger synchronous intent review. Reviews raise or lower risk within bounds, and identical reviews are cached within a run. Headphone purchases from an unknown merchant retain an approval score of 60–84. Most clear transactions avoid model latency and cost; the $500 injected payout is blocked by rules, while the $19.99 charger is allowed by rules.</p>
          <div className="docs-callout">
            <strong>Fail closed on review timeout</strong>
            <p>Review errors, invalid responses, and a four-second timeout require human approval. The payment stays paused in Enforce until approved or denied.</p>
          </div>
          <h3>Structured decision evidence</h3>
          <p>The live feed displays a rule-template title based on the strongest risk factor, point chips with stable reason codes, highlighted original source evidence, and decision latency. A synchronous AI review adds one sentence of at most 20 English words. Complete review rationale is available in event details, collapsed by default. There is no background explanation call.</p>
          <p>Median decision latency measures recorded decision samples; AI-reviewed decisions measures actual synchronous model attempts, including failed attempts and excluding cached reviews and rules fallback. Older events without these measurements are excluded. Flagged events can be filtered by reason code.</p>
          <h3>Monitor and Enforce</h3>
          <p><strong>Monitor</strong> computes and records decisions while allowing tool execution. <strong>Enforce</strong> blocks rejected calls and pauses requests requiring approval. Every call retains the mode used for evaluation.</p>
        </section>
        <section id="policies">
          <span className="eyebrow">POLICY REFERENCE</span>
          <h2>Five payment controls</h2>
          <p>Edit the initial YAML policy, or update values in the console for immediate application.</p>
          <div className="docs-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>Behavior</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Single-transaction limit</td>
                  <td>Block above the configured amount.</td>
                </tr>
                <tr>
                  <td>Period budget</td>
                  <td>Block when daily or weekly spend plus reservations exceeds the budget.</td>
                </tr>
                <tr>
                  <td>Merchant / payee allowlist</td>
                  <td>Add risk for unknown recipients; optionally hard-block.</td>
                </tr>
                <tr>
                  <td>Category restrictions</td>
                  <td>Block restricted categories from catalog metadata.</td>
                </tr>
                <tr>
                  <td>Risk-based approval</td>
                  <td>Auto-approve limit, review floor, scoring weights, velocity settings, and approval/block score thresholds.</td>
                </tr>
              </tbody>
            </table>
          </div>
          <CodeBlock code={policyYaml} language="yaml" />
          <p>The fifth policy includes <strong>Auto-approve limit</strong> (<code>auto_approve_threshold</code>, default $50), every factor weight, the review floor, and velocity count/window. Factor parameter links open the corresponding control in Policies. Each of the five policies has an enabled switch, and edits are audited.</p>
          <p>The allowlist uses named recipient rows with validated email or domain addresses, individual removal, and an optional hard-block toggle. The simulated Volt seller is outside the default allowlist: its small, budget-aligned charger order still allows by rules. Higher-value unknown-merchant headphones retain an approval score of 60–84.</p>
        </section>
        <section id="audit">
          <span className="eyebrow">AUDIT & INTEGRATIONS</span>
          <h2>Review every decision</h2>
          <p>SQLite records payment tools, amounts, recipients, decisions, risk factors, provenance, stage latency, approval results, and PayPal IDs and statuses. Mode and policy changes are audited.</p>
          <CodeBlock code={'curl http://localhost:8000/api/audit\ncurl "http://localhost:8000/api/audit/flagged?reason_code=untrusted_source"\ncurl -N "http://localhost:8000/api/events?audience=console"'} language="shell" />
          <h3>Live state over SSE</h3>
          <p>Checkout displays a neutral Waiting for approval state with the amount, recipient, Review in Airlock link, and Stop task. Start stays disabled while the request waits. After approval, the step shows Approved · Order created. The client and console subscribe to backend state. The client receives tool results; risk evidence stays in the console projection. Approvals are single use. Cancelling a task does not reverse a submitted payment.</p>
          <div className="docs-note">
            <Icon name="docs" size={19} />
            <p>Webhooks are not implemented. Use the audit API and server-sent events. This release runs in one process; durable audit retention requires a persistent disk.</p>
          </div>
          <h3>Channel3 catalog</h3>
          <p>Channel3 supplies catalog search data. A simulated marketplace listing adds the poisoned checkout policy. Purchases always use the configured PayPal sandbox shop recipient; real catalog merchants are never paid.</p>
          <p>Created orders are not captured payments. Accepted payout batches can remain pending. The local sandbox wallet tracks submitted requests and order reservations, not settled account funds.</p>
        </section>
        <section id="threat-model">
          <span className="eyebrow">THREAT MODEL</span>
          <h2>External content is untrusted</h2>
          <p>Product pages and tool output can contain instructions that manipulate an agent into proposing an unauthorized payment. The firewall checks payment authority independently from the shopping model.</p>
          <div className="docs-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Agent model</th>
                  <th>Injected payout calls / trials</th>
                </tr>
              </thead>
              <tbody>
                {[['Shopping model A', '4 / 4'], ['Shopping model B', '1 / 4'], ['Shopping model C', '0 / 4'], ['Shopping model D', '0 / 4'], ['Shopping model E', '0 / 2']].map(([model, result]) => <tr key={model}>
                  <td>
                    <code>
                      {model}
                    </code>
                  </td>
                  <td>
                    {result}
                  </td>
                </tr>)}
              </tbody>
            </table>
          </div>
          <p className="docs-small">Small, author-reported samples from November 9, 2026. Model identities and test context are listed in the <a href="https://gitlab.com/neozx/airlock#threat-model-and-limits" target="_blank" rel="noopener noreferrer">project README</a>. Success means a $500 injected payout tool call, not settlement. Tests were not rerun for this redesign. Zero observed calls does not establish immunity.</p>
          <h3>Scope</h3>
          <p>This is a single-workspace sandbox demonstration without authentication or multi-tenancy. It illustrates the payment execution boundary, not a production authorization service.</p>
        </section>
        <footer className="docs-footer"><span>Deployed on Render · Audit log powered by AG Grid · Product data from Channel3</span><a href="/console">Return to console<Icon name="arrow" size={15} /></a></footer>
      </main>
      <aside className="docs-toc">
        <span>ON THIS PAGE</span>
        {docSections.map(section => <a href={`#${section.id}`} key={section.id}>
          {section.name}
        </a>)}
      </aside>
    </div>
  </div>;
}
