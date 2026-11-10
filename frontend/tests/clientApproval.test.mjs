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
afterEach(() => { globalThis.fetch = originalFetch; });
async function load(entry, react) {
  const result = await build({ entryPoints: [path.join(root, 'src', entry)], bundle: true, write: false,
    platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react-dom/server'], loader: { '.css': 'empty' } });
  const compiled = new Module(path.join(root, 'tests', entry + '.cjs'));
  compiled.filename = path.join(root, 'tests', entry + '.cjs');
  compiled.paths = Module._nodeModulePaths(root);
  const require = compiled.require.bind(compiled);
  compiled.require = name => name === 'react' && react ? { ...React, ...react } : require(name);
  compiled._compile(result.outputFiles[0].text, compiled.filename);
  return compiled.exports;
}
function hooks(overrides = []) {
  const states = [], refs = [];
  let cursor = 0, refCursor = 0;
  return { states, reset() { cursor = 0; refCursor = 0; },
    useState(initial) { const index = cursor++; if (!(index in states)) states[index] = index in overrides ? overrides[index] : typeof initial === 'function' ? initial() : initial; return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }]; },
    useRef(initial) { const index = refCursor++; return refs[index] ||= { current: initial }; }, useEffect() {} };
}
function find(node, predicate) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const child of node) { const match = find(child, predicate); if (match) return match; } return; }
  if (predicate(node)) return node;
  return find(node.props?.children, predicate);
}
const pending = { id: 'approval-task', status: 'awaiting_approval', revision: 3,
  messages: [{ role: 'user', content: 'Buy headphones under $300' }], approvals: [{ id: 'approval-1', status: 'pending', tool: 'create_order', amount: '249.99', payee: 'shop@example.com' }],
  events: [{ id: 'checkout', type: 'checkout', tool: 'create_order', amount: '249.99', payee: 'shop@example.com' }, { id: 'pending', type: 'client_status', status: 'awaiting_approval' }] };
const state = runs => ({ config: { paypal_mode: 'MOCK' }, runs, audit: { rows: [] }, wallet: { balance: '1000' } });

test('approval lives inside checkout with amount, recipient, console link, and a neutral waiting state', async () => {
  const { ClientActivity } = await load('ClientActivity.tsx');
  const html = renderToStaticMarkup(React.createElement(ClientActivity, { run: pending, onStop() {} }));
  assert.match(html, /Waiting for approval/);
  assert.match(html, /\$249\.99 → shop@example.com/);
  assert.match(html, /Approval requested · The agent will continue once it’s approved\./);
  assert.match(html, /href="\/console\?approval=approval-1#approvals"/);
  assert.match(html, /Review in Airlock ↗/);
  assert.match(html, /Stop task/);
  assert.match(html, /waiting-approval/);
  assert.doesNotMatch(html, /Current step|role="alert"|amber|task-status-notice/);
});

test('a restored pending approval displays checkout even before the event history arrives', async () => {
  const { ClientActivity } = await load('ClientActivity.tsx');
  const html = renderToStaticMarkup(React.createElement(ClientActivity, { run: { ...pending, events: [] }, onStop() {} }));
  assert.match(html, /Waiting for approval/);
  assert.match(html, /\$249\.99 → shop@example.com/);
  assert.match(html, /Review in Airlock ↗/);
});

test('approved checkout becomes a checked order creation receipt without claiming settlement', async () => {
  const { ClientActivity } = await load('ClientActivity.tsx');
  const html = renderToStaticMarkup(React.createElement(ClientActivity, { run: {
    ...pending, status: 'completed', approvals: [{ ...pending.approvals[0], status: 'approved' }], events: [
      ...pending.events, { id: 'result', type: 'tool_result', status: 'succeeded', paypal_id: 'MOCK-ORDER', mock: true },
    ],
  } }));
  assert.match(html, /Approved · Order created/);
  assert.match(html, /activity-step-icon complete[^>]*>✓/);
  assert.match(html, /MOCK-ORDER/);
  assert.doesNotMatch(html, /Waiting for approval|Review in Airlock|Stop task|badge amber/);
  assert.match(html, /acceptance does not confirm settlement/);
});

test('waiting disables Start and only a real conflicting submit adds a neutral input hint', async () => {
  const react = hooks([state([pending]), true, 'live', '']);
  const { Client } = await load('App.tsx', react);
  const render = () => { react.reset(); return Client(); };
  let tree = render();
  const start = find(tree, node => node.type === 'button' && node.props.className === 'button primary');
  assert.equal(start.props.disabled, true);
  assert.ok(find(tree, node => node.props?.className === 'task-running-note'));
  assert.equal(find(tree, node => node.props?.className === 'task-conflict-note'), undefined);
  assert.equal(find(tree, node => node.props?.className?.includes('task-status-notice')), undefined);
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error('No request expected'); };
  await find(tree, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  tree = render();
  const hint = find(tree, node => node.props?.className === 'task-conflict-note');
  assert.match(hint.props.children, /waiting for approval/);
  assert.equal(hint.props.role, 'status');
  assert.equal(calls, 0);
  react.states[0] = state([{ ...pending, status: 'completed', revision: 5 }]);
  assert.equal(find(render(), node => node.props?.className === 'task-conflict-note'), undefined);
});

test('checkout Stop task invokes cancellation and keeps the stopped response ahead of stale SSE', async () => {
  const react = hooks([state([pending]), true, 'live', '']);
  const { Client } = await load('App.tsx', react);
  const render = () => { react.reset(); return Client(); };
  const tree = render();
  const activity = find(tree, node => node.props?.run);
  let called;
  globalThis.fetch = async url => { called = url; return new Response(JSON.stringify({ ...pending, status: 'cancelled', revision: 5 })); };
  activity.props.onStop();
  await new Promise(setImmediate);
  assert.equal(called, '/api/runs/approval-task/cancel?audience=client');
  const stopped = find(render(), node => node.props?.run);
  assert.equal(stopped.props.run.status, 'cancelled');
  assert.equal(stopped.props.run.revision, 5);
});

test('a recovered 409 approval is shown as a neutral conflict hint instead of an error banner', async () => {
  const react = hooks([state([]), true, 'live', '']);
  const { Client } = await load('App.tsx', react);
  const render = () => { react.reset(); return Client(); };
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: { message: 'Task is waiting for approval', run: pending } }), { status: 409 });
  await find(render(), node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  const tree = render();
  assert.equal(find(tree, node => node.props?.run)?.props.run.id, pending.id);
  assert.match(find(tree, node => node.props?.className === 'task-conflict-note').props.children, /waiting for approval/);
  assert.equal(find(tree, node => node.props?.message === 'Task is waiting for approval'), undefined);
});
