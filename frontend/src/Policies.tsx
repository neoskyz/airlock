import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { Icon, Badge } from './ui';
import type { Config, Policy } from './types';
import { validRecipientAddress, recipientLabel, riskWeightLabels } from './policyPresentation';
const policyDefinitions = [{
  key: 'single_transaction',
  title: 'Single-transaction limit',
  description: 'Block a payment when its amount exceeds the per-transaction limit.',
  tag: 'Hard limit'
}, {
  key: 'period_budget',
  title: 'Period budget',
  description: 'Block payments that exceed the daily or weekly budget. Pending approvals reserve funds.',
  tag: 'Hard limit'
}, {
  key: 'allowlist',
  title: 'Merchant / payee allowlist',
  description: 'Unknown recipients add risk. Enable hard-block mode to reject them immediately.',
  tag: 'Recipient trust'
}, {
  key: 'categories',
  title: 'Category restrictions',
  description: 'Block restricted product categories using catalog metadata.',
  tag: 'Hard limit'
}, {
  key: 'risk_threshold',
  title: 'Risk-based approval',
  description: 'Require human approval or block based on the final explainable risk score.',
  tag: 'Human review'
}];
type Props = {
  config: Config;
  online: boolean;
  onError: (error: string) => void;
  onReset: () => void;
  resetBusy: boolean;
  canReset: boolean;
};
export function Policies({
  config,
  online,
  onError,
  onReset,
  resetBusy,
  canReset
}: Props) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState('');
  const [retry, setRetry] = useState(0);
  const revision = useRef(0);
  const saveLock = useRef(false);
  const [addingRecipient, setAddingRecipient] = useState(false);
  const [recipientName, setRecipientName] = useState('');
  const [recipientAddress, setRecipientAddress] = useState('');
  const [recipientError, setRecipientError] = useState('');
  const parameter = new URLSearchParams(typeof location === 'undefined' ? '' : location.search).get('parameter');
  useEffect(() => {
    if (!policy || !parameter || typeof document === 'undefined') return;
    const element = document.getElementById(`policy-${parameter}`);
    element?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    element?.focus({ preventScroll: true });
  }, [policy, parameter]);
  async function addRecipient() {
    const address = recipientAddress.trim().toLowerCase();
    if (!validRecipientAddress(address)) { setRecipientError('Enter a valid email address or domain.'); return; }
    if (policy?.allowed_payees.some(value => value.toLowerCase() === address)) { setRecipientError('This recipient is already listed.'); return; }
    if (!policy) return;
    setRecipientError('');
    const applied = await update({ allowed_payees: [...policy.allowed_payees, address], allowed_payee_names: { ...policy.allowed_payee_names, [address]: recipientName.trim() || recipientLabel(address) } }, 'allowed_payees');
    if (!applied) return;
    setRecipientAddress(''); setRecipientName(''); setAddingRecipient(false);
  }
  async function removeRecipient(address: string) {
    if (!policy) return;
    const names = { ...policy.allowed_payee_names }; delete names[address];
    await update({ allowed_payees: policy.allowed_payees.filter(value => value !== address), allowed_payee_names: names }, 'allowed_payees');
  }
  useEffect(() => {
    const controller = new AbortController();
    const currentRevision = ++revision.current;
    setLoadError('');
    void request<Policy>('/api/policies', {
      signal: controller.signal
    }).then(value => {
      if (!controller.signal.aborted && currentRevision === revision.current) setPolicy(value);
    }).catch(error => {
      if (!controller.signal.aborted) setLoadError(`Could not load policies: ${(error as Error).message}`);
    });
    return () => controller.abort();
  }, [config.policy_yaml, retry]);
  async function update(changes: Partial<Policy>, key: string) {
    if (!policy || saveLock.current || !online) return;
    saveLock.current = true;
    const currentRevision = ++revision.current;
    setSaving(true);
    setSaved('');
    try {
      // PATCH only the fields being edited. A stale copy of an unrelated
      // policy must never overwrite another console operator's changes.
      const value = await request<Policy>('/api/policies', {
        method: 'PATCH',
        body: JSON.stringify(changes)
      });
      if (currentRevision === revision.current) setPolicy(value);
      setSaved(key);
      setDrafts(current => {
        const next = {
          ...current
        };
        delete next[key];
        return next;
      });
      onError('');
      return true;
    } catch (error) {
      onError(`Policy update failed: ${(error as Error).message}`);
      return false;
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }
  function field(key: string, label: string, value: string | number, commit: (text: string) => Partial<Policy>, type = 'number') {
    const score = ['risk_approval_threshold', 'risk_block_threshold', 'review_min_score'].includes(key);
    const integer = score || key.startsWith('risk_weights.') || key.startsWith('velocity_');
    return <label className="policy-field">
    {label}
    <input id={`policy-${key}`} aria-label={label} type={type} min={type === 'number' ? integer ? key.startsWith('risk_weights.') ? 0 : 1 : key === 'auto_approve_threshold' ? 0 : 0.01 : undefined} max={key === 'review_min_score' ? 84 : key === 'risk_approval_threshold' ? 99 : score || key.startsWith('risk_weights.') ? 100 : undefined} step={type === 'number' ? integer ? 1 : '0.01' : undefined} value={drafts[key] ?? value} disabled={saving || !online} onChange={event => setDrafts(current => ({
        ...current,
        [key]: event.target.value
      }))} onBlur={event => {
        const text = drafts[key];
        if (text === undefined || text === String(value)) return;
        if (type === 'number' && !text.trim() || !event.currentTarget.validity.valid) {
          onError(`Enter a valid value for ${label.toLowerCase()}.`);
          return;
        }
        void update(commit(text), key);
      }} onKeyDown={event => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }} />
  </label>;
  }
  if (!policy) return <div className="empty-panel" role={loadError ? 'alert' : 'status'}>
    <h3>
      {loadError ? 'Policies unavailable' : 'Loading policies…'}
    </h3>
    {loadError && <>
      <p>
        {loadError}
      </p>
      <button className="button secondary" onClick={() => setRetry(value => value + 1)}>Retry</button>
    </>}
  </div>;
  return <div className="policies-list" aria-busy={saving}>
    {loadError && <div className="error-banner" role="alert">
      <span>{loadError} Displaying the last loaded policy.</span>
      <button onClick={() => setRetry(value => value + 1)}>Retry</button>
    </div>}
    {policyDefinitions.map((definition, index) => <section id={`policy-${definition.key}`} tabIndex={-1} key={definition.key} className={`policy-card ${policy.enabled?.[definition.key] === false ? 'disabled' : ''}`}>
      <div className="policy-card-header">
        <span className="policy-number">0{index + 1}</span>
        <div>
          <h3>
            {definition.title}
          </h3>
          <p>
            {definition.description}
          </p>
        </div>
        <Badge>
          {definition.tag}
        </Badge>
        <button className={`toggle ${policy.enabled?.[definition.key] !== false ? 'on' : ''}`} role="switch" aria-checked={policy.enabled?.[definition.key] !== false} aria-label={`Enable ${definition.title}`} disabled={saving || !online} onClick={() => void update({
          enabled: {
            [definition.key]: policy.enabled?.[definition.key] === false
          }
        }, definition.key)}>
          <span />
        </button>
      </div>
      <div className="policy-values">
        {definition.key === 'single_transaction' && field('single_transaction_limit', 'Maximum amount · USD', policy.single_transaction_limit, value => ({
          single_transaction_limit: value
        }))}
        {definition.key === 'period_budget' && <>
          {field('daily_limit', 'Budget amount · USD', policy.daily_limit, value => ({
            daily_limit: value
          }))}
          <label className="policy-field">Budget period
            <select value={policy.period} disabled={saving || !online} onChange={event => void update({
              period: event.target.value as Policy['period']
            }, definition.key)}>
              <option value="daily">Daily · UTC</option>
              <option value="weekly">Weekly · UTC</option>
            </select>
          </label>
        </>}
        {definition.key === 'allowlist' && <>
          <div className="recipient-manager" id="policy-allowed_payees" tabIndex={-1}>
            <div className="recipient-list-header"><span>Allowed recipients</span><button type="button" className="button secondary" disabled={saving || !online} onClick={() => setAddingRecipient(value => !value)}>+ Add recipient</button></div>
            {addingRecipient && <div className="recipient-add-form" role="group" aria-label="Add recipient" onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); void addRecipient(); } }}>
              <label>Name<input aria-label="Recipient name" value={recipientName} disabled={saving || !online} onChange={event => setRecipientName(event.target.value)} placeholder="Recipient name" maxLength={100} /></label>
              <label>Email or domain<input aria-label="Recipient address" value={recipientAddress} disabled={saving || !online} onChange={event => { setRecipientAddress(event.target.value); setRecipientError(''); }} placeholder="shop@example.com or merchant.example" maxLength={254} /></label>
              <button type="button" className="button primary" disabled={saving || !online} onClick={() => void addRecipient()}>Add</button>
              {recipientError && <p className="recipient-error" role="alert">{recipientError}</p>}
            </div>}
            <ul className="recipient-list">{policy.allowed_payees.map(address => <li key={address}><div><strong>{policy.allowed_payee_names?.[address] || recipientLabel(address)}</strong><span title={address}>{address}</span></div><button type="button" disabled={saving || !online} onClick={() => void removeRecipient(address)} aria-label={`Remove ${address}`}>Remove</button></li>)}</ul>
            {!policy.allowed_payees.length && <p className="recipient-empty">No recipients added.</p>}
          </div>
          <div className="policy-toggle-field" id="policy-unknown_payee_action"><span>Hard-block unknown payees</span><button className={`toggle ${policy.unknown_payee_action === 'BLOCK' ? 'on' : ''}`} role="switch" aria-checked={policy.unknown_payee_action === 'BLOCK'} aria-label="Hard-block unknown payees" disabled={saving || !online} onClick={() => void update({ unknown_payee_action: policy.unknown_payee_action === 'BLOCK' ? 'REQUIRE_APPROVAL' : 'BLOCK' }, 'unknown_payee_action')}><span /></button></div>
        </>}
        {definition.key === 'categories' && field('blocked_categories', 'Restricted categories · comma separated', policy.blocked_categories.join(', '), value => ({
          blocked_categories: value.split(',').map(item => item.trim()).filter(Boolean)
        }), 'text')}
        {definition.key === 'risk_threshold' && <>
          {field('review_min_score', 'Start AI review at score', policy.review_min_score ?? 30, value => ({ review_min_score: Number(value) }))}
          {field('risk_approval_threshold', 'Require approval at score', policy.risk_approval_threshold, value => ({
            risk_approval_threshold: Number(value)
          }))}
          {field('risk_block_threshold', 'Block at score', policy.risk_block_threshold, value => ({
            risk_block_threshold: Number(value)
          }))}
          {field('auto_approve_threshold', 'Auto-approve limit · USD', policy.auto_approve_threshold, value => ({
            auto_approve_threshold: value
          }))}
          <div className="risk-parameter-group"><h4>Risk factor weights</h4><p>Points added by each factor. Changes apply to the next policy evaluation.</p><div className="risk-parameter-fields">{Object.entries(policy.risk_weights || {}).map(([key, value]) => <div key={key}>{field(`risk_weights.${key}`, riskWeightLabels[key] || key.replaceAll('_', ' '), value, text => ({ risk_weights: { [key]: Number(text) } }))}</div>)}</div></div>
          <div className="risk-parameter-group"><h4>Payment velocity</h4><div className="risk-parameter-fields">{field('velocity_call_threshold', 'Calls before velocity risk', policy.velocity_call_threshold ?? 3, value => ({ velocity_call_threshold: Number(value) }))}{field('velocity_window_seconds', 'Velocity window · seconds', policy.velocity_window_seconds ?? 60, value => ({ velocity_window_seconds: Number(value) }))}</div></div>
        </>}
      </div>
    </section>)}
    <div className="policy-save-note" role="status">
      <Icon name="check" size={16} />
      <span>
        {saving ? 'Applying policy…' : saved ? 'Policy updated. The change is effective immediately and recorded in the audit log.' : 'Edits apply when you leave a field. All policy changes are audited.'}
      </span>
    </div>
    <section className="sandbox-reset">
      <div>
        <h3>Sandbox data</h3>
        <p>Clear local audit history, period budget usage, wallet ledger, and completed tasks before recording. Submitted PayPal requests remain in the sandbox.</p>
        {!canReset && !resetBusy && <p className="reset-hint">Connect to the server and finish active tasks before resetting.</p>}
      </div>
      <button type="button" className="button secondary reset-button" disabled={!canReset || resetBusy || saving} onClick={onReset}>
        {resetBusy ? 'Resetting…' : 'Reset sandbox data'}
      </button>
    </section>
  </div>;
}
