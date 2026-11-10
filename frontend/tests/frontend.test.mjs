import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import Module from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = fileURLToPath(new URL('..', import.meta.url));
const originalFetch = globalThis.fetch;
const originalEventSource = globalThis.EventSource;
afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.EventSource = originalEventSource;
});

async function load(entry, react) {
  const result = await build({
    entryPoints: [path.join(root, 'src', entry)], bundle: true, write: false,
    platform: 'node', format: 'cjs', jsx: 'automatic',
    external: ['react', 'react-dom/server'], loader: { '.css': 'empty' },
  });
  const filename = path.join(root, 'tests', entry + '.cjs');
  const compiled = new Module(filename);
  compiled.filename = filename;
  compiled.paths = Module._nodeModulePaths(root);
  const require = compiled.require.bind(compiled);
  compiled.require = name => name === 'react' && react ? react : require(name);
  compiled._compile(result.outputFiles[0].text, compiled.filename);
  return compiled.exports;
}

function hooks(overrides = []) {
  const states = [];
  const refs = [];
  const effects = [];
  let stateCursor = 0;
  let refCursor = 0;
  return {
    effects, states,
    reset() { stateCursor = 0; refCursor = 0; },
    useState(initial) {
      const index = stateCursor++;
      if (!(index in states)) states[index] = index in overrides ? overrides[index] : initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
    },
    useRef(initial) {
      const index = refCursor++;
      refs[index] ||= { current: initial };
      return refs[index];
    },
    useEffect(effect) { effects.push(effect); },
  };
}

function find(node, predicate) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const child of node) { const match = find(child, predicate); if (match) return match; }
    return;
  }
  if (predicate(node)) return node;
  return find(node.props?.children, predicate);
}

const samplePolicy = {
  currency: 'USD', single_transaction_limit: '1000', daily_limit: '2000', period: 'weekly',
  auto_approve_threshold: '50', allowed_payees: ['shop@example.com'], unknown_payee_action: 'REQUIRE_APPROVAL',
  blocked_categories: ['crypto'], risk_approval_threshold: 60, risk_block_threshold: 85,
  enabled: { single_transaction: true, period_budget: true, allowlist: true, categories: true, risk_threshold: true },
};

for (const [key, draft, expected] of [
  ['single_transaction_limit', '75', { single_transaction_limit: '75' }],
  ['allowed_payees', '', { allowed_payees: [] }],
]) {
  test(`policy ${key} changes only its own field`, async () => {
    const react = hooks([samplePolicy, false, '', { [key]: draft }]);
    const { Policies } = await load('Policies.tsx', react);
    let body;
    globalThis.fetch = async (_url, options) => {
      body = JSON.parse(options.body);
      return new Response(JSON.stringify({ ...samplePolicy, ...body }));
    };
    const tree = Policies({ config: { policy_yaml: '' }, online: true, onError() {}, onReset() {}, resetBusy: false, canReset: true });
    const label = key === 'allowed_payees' ? 'Allowed recipients · comma separated' : 'Maximum amount · USD';
    const input = find(tree, node => node.type === 'input' && node.props['aria-label'] === label);
    input.props.onBlur({ currentTarget: { validity: { valid: true } } });
    await new Promise(setImmediate);
    assert.deepEqual(body, expected);
  });
}

test('policy toggles do not send stale values for other controls', async () => {
  const react = hooks([samplePolicy]);
  const { Policies } = await load('Policies.tsx', react);
  let body;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return new Response(JSON.stringify(samplePolicy));
  };
  const tree = Policies({ config: { policy_yaml: '' }, online: true, onError() {}, onReset() {}, resetBusy: false, canReset: true });
  find(tree, node => node.props?.['aria-label'] === 'Enable Single-transaction limit').props.onClick();
  await new Promise(setImmediate);
  assert.deepEqual(body, { enabled: { single_transaction: false } });
});

test('an open SSE connection waits for a valid state before enabling actions', async () => {
  const react = hooks();
  let source;
  globalThis.EventSource = class {
    constructor() { source = this; }
    addEventListener(name, callback) { this[name] = callback; }
    close() {}
  };
  const { useLive } = await load('useLive.ts', react);
  useLive('console');
  const cleanup = react.effects[0]();
  source.onopen?.();
  assert.equal(react.states[1], false);
  assert.equal(react.states[2], 'connecting');
  source.state({ data: '{}' });
  assert.equal(react.states[1], false);
  assert.equal(react.states[2], 'reconnecting');
  source.state({ data: JSON.stringify({ config: {}, runs: [], audit: { rows: [] }, wallet: {} }) });
  assert.equal(react.states[1], true);
  assert.equal(react.states[2], 'live');
  cleanup();
});

test('a timed out request explains that its outcome must be checked before retrying', async () => {
  const { request } = await load('api.ts');
  globalThis.fetch = async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
  });
  const signal = AbortSignal.timeout(5);
  // Keep the event loop alive while Node's unref'ed AbortSignal timer runs.
  const keepAlive = setTimeout(() => {}, 100);
  try {
    await assert.rejects(request('/api/approvals/example', { method: 'POST', signal }), /Check the current status before retrying/);
  } finally { clearTimeout(keepAlive); }
});

test('request preserves Headers objects and a caller cancellation signal', async () => {
  const { request } = await load('api.ts');
  const controller = new AbortController();
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.headers.get('X-Request-Test'), 'kept');
    controller.abort();
    assert.equal(options.signal.aborted, true);
    throw options.signal.reason;
  };
  await assert.rejects(request('/api/audit', { headers: new Headers({ 'X-Request-Test': 'kept' }), signal: controller.signal }), { name: 'AbortError' });
});

test('completed runs with no payment result remain unconfirmed', async () => {
  const { ClientActivity } = await load('ClientActivity.tsx');
  const html = renderToStaticMarkup(React.createElement(ClientActivity, {
    run: { id: 'run', status: 'completed', events: [{ id: 'checkout', type: 'checkout', tool: 'create_order', amount: '19.99', message: 'Submitting' }] },
  }));
  assert.match(html, /Payment result unavailable/);
  assert.match(html, /Unconfirmed/);
  assert.doesNotMatch(html, /payment-operation complete/);
});

test('catalog prices retain their currency and audio image fallback', async () => {
  const { ClientActivity } = await load('ClientActivity.tsx');
  const html = renderToStaticMarkup(React.createElement(ClientActivity, {
    run: { id: 'run', status: 'completed', events: [{ id: 'catalog', type: 'products', message: 'Products', products: [{ id: 'audio', name: 'Headphones', price: '249.99', currency: 'EUR', merchant: 'audio.example', description: 'Audio', category: 'audio' }] }] },
  }));
  assert.match(html, /€249.99/);
  assert.match(html, /Headphones image unavailable/);
});


test('audit chunk failure has a reload action for the latest deployment', async () => {
  const { AuditLogBoundary } = await load('AuditLogBoundary.tsx');
  const boundary = new AuditLogBoundary({ children: 'Audit table' });
  assert.equal(boundary.render(), 'Audit table');
  boundary.state = AuditLogBoundary.getDerivedStateFromError(new Error('Chunk unavailable'));
  const fallback = boundary.render();
  const html = renderToStaticMarkup(fallback);
  assert.match(html, /Audit table unavailable/);
  assert.match(html, /role="alert"/);
  const previousWindow = globalThis.window;
  let reloaded = false;
  globalThis.window = { location: { reload() { reloaded = true; } } };
  try {
    find(fallback, node => node.type === 'button').props.onClick();
    assert.equal(reloaded, true);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});
