import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { Icon, Badge } from './ui';
import type { Config, Policy } from './types';
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
  title: 'Risk score thresholds',
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
    } catch (error) {
      onError(`Policy update failed: ${(error as Error).message}`);
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }
  function field(key: string, label: string, value: string | number, commit: (text: string) => Partial<Policy>, type = 'number') {
    const score = key.startsWith('risk_');
    return <label className="policy-field">
    {label}
    <input aria-label={label} type={type} min={type === 'number' ? score ? 1 : key === 'auto_approve_threshold' ? 0 : 0.01 : undefined} max={score ? 100 : undefined} step={type === 'number' ? score ? 1 : '0.01' : undefined} value={drafts[key] ?? value} disabled={saving || !online} onChange={event => setDrafts(current => ({
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
    {policyDefinitions.map((definition, index) => <section key={definition.key} className={`policy-card ${policy.enabled?.[definition.key] === false ? 'disabled' : ''}`}>
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
          {field('allowed_payees', 'Allowed recipients · comma separated', policy.allowed_payees.join(', '), value => ({
            allowed_payees: value.split(',').map(item => item.trim()).filter(Boolean)
          }), 'text')}
          <label className="checkbox-field"><input type="checkbox" checked={policy.unknown_payee_action === 'BLOCK'} disabled={saving || !online} onChange={event => void update({
              unknown_payee_action: event.target.checked ? 'BLOCK' : 'REQUIRE_APPROVAL'
            }, definition.key)} />Hard-block unknown payees</label>
        </>}
        {definition.key === 'categories' && field('blocked_categories', 'Restricted categories · comma separated', policy.blocked_categories.join(', '), value => ({
          blocked_categories: value.split(',').map(item => item.trim()).filter(Boolean)
        }), 'text')}
        {definition.key === 'risk_threshold' && <>
          {field('risk_approval_threshold', 'Require approval at score', policy.risk_approval_threshold, value => ({
            risk_approval_threshold: Number(value)
          }))}
          {field('risk_block_threshold', 'Block at score', policy.risk_block_threshold, value => ({
            risk_block_threshold: Number(value)
          }))}
          {field('auto_approve_threshold', 'Amount risk reference · USD', policy.auto_approve_threshold, value => ({
            auto_approve_threshold: value
          }))}
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
