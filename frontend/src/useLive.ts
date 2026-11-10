import { useEffect, useState } from 'react';
import type { LiveState } from './types';

const initial: LiveState = {
  config: {
    mode: 'Monitor', demo_mode: true, agent_mode: 'compromised',
    live_available: false, llm_degraded: false, paypal_mode: 'MOCK', policy_yaml: '',
  },
  runs: [],
  audit: { rows: [], stats: { blocked_amount: 0, allowed_amount: 0 } },
  wallet: { balance: 0, currency: 'USD', label: 'Sandbox · local balance', source: 'local_ledger' },
};

export function useLive(audience: 'client' | 'console') {
  const [state, setState] = useState(initial);
  const [ready, setReady] = useState(false);
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting'>('connecting');
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    const source = new EventSource(`/api/events?audience=${audience}`);
    source.addEventListener('state', event => {
      if (!alive) return;
      try {
        const value = JSON.parse((event as MessageEvent).data) as LiveState;
        if (!value.config || !Array.isArray(value.runs) || !Array.isArray(value.audit?.rows) || !value.wallet) {
          throw new Error('Incomplete live state');
        }
        setState(value);
        setReady(true);
        setConnection('live');
        setError('');
      } catch {
        setConnection('reconnecting');
        setError('The latest live update could not be read. Waiting for a valid server update.');
      }
    });
    // An open transport is not yet a synchronized workspace. Only a valid
    // state event enables actions that depend on mode, policies, or active runs.
    source.onerror = () => { if (alive) setConnection('reconnecting'); };
    return () => { alive = false; source.close(); };
  }, [audience]);

  return { state, ready, connection, error };
}
