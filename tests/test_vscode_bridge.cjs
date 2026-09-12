const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {startBridge} = require('../extensions/vscode-bots/bridge');
const token = 'a'.repeat(64);
const model = {id: 'test-model', vendor: 'copilot', name: 'Synthetic model'};
const body = {prompt: 'synthetic task', model: model.id, timeout_seconds: 10};

async function setup(t, overrides = {}) {
  const bridge = await startBridge({token, getModel: () => model, isEnabled: () => true,
    async *runModel() { yield 'synthetic '; yield 'answer'; }, ...overrides});
  t.after(() => bridge.close());
  const url = `http://127.0.0.1:${bridge.port}`;
  return {bridge, url, call: (route = '/task', task = body, headers = {}) => fetch(url + route,
    {method: task === null ? 'GET' : 'POST', headers: {Authorization: `Bearer ${token}`, ...headers},
      body: task === null ? undefined : JSON.stringify(task)})};
}

test('authenticated task returns answer-only stream with an explicit terminal result', async t => {
  const {call} = await setup(t);
  const response = await call();
  const events = (await response.text()).trim().split('\n').map(JSON.parse);
  assert.equal(response.status, 200);
  assert.equal(events.at(-1).result, 'synthetic answer');
  assert.equal(events.at(-1).usage, null);
  assert.equal(events.at(-1).is_error, false);
});

test('origin and invalid authorization are rejected without a model call', async t => {
  let calls = 0;
  const {call} = await setup(t, {async *runModel() { calls++; yield 'wrong'; }});
  for (const headers of [{Origin: 'https://example.com'}, {Authorization: 'wrong'},
    {Authorization: 'Bearer ' + 'é'.repeat(64)}]) {
    const response = await call('/task', body, headers);
    assert.equal(response.status, 403); await response.text();
  }
  assert.equal(calls, 0);
});

test('disabled bridge, changed model and oversized brief do not start inference', async t => {
  let calls = 0;
  const {call} = await setup(t, {async *runModel() { calls++; yield 'wrong'; }});
  for (const [task, status] of [[{...body, model: 'other'}, 409],
    [{...body, prompt: 'x'.repeat(33000)}, 400], [{...body, timeout_seconds: 1}, 400]]) {
    const response = await call('/task', task); assert.equal(response.status, status); await response.text();
  }
  const off = await setup(t, {isEnabled: () => false});
  assert.equal((await off.call()).status, 409);
  assert.equal(calls, 0);
});

test('a concurrent request is rejected and disconnect cancels the active task', async t => {
  let cancelled = false;
  const {call} = await setup(t, {async *runModel(prompt, signal) {
    yield 'partial';
    await new Promise(resolve => signal.addEventListener('abort', () => { cancelled = true; resolve(); }, {once: true}));
  }});
  const active = await call();
  const second = await call();
  assert.equal(second.status, 409); await second.text();
  await active.body.cancel();
  for (let i = 0; i < 30 && !cancelled; i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(cancelled, true);
});

test('permission errors preserve a useful recovery cause without raw error text', async t => {
  const {call} = await setup(t, {async *runModel() {
    throw Object.assign(new Error('PRIVATE provider response'), {code: 'NoPermissions'});
  }});
  const text = await (await call()).text();
  const result = JSON.parse(text.trim().split('\n').at(-1));
  assert.equal(result.is_error, true);
  assert.match(result.errors[0], /Allow Agent Orchestrator/);
  assert.doesNotMatch(text, /PRIVATE/);
});

test('status is content-free and cannot dispatch a task', async t => {
  const {call} = await setup(t);
  const status = await (await call('/status', null)).json();
  assert.equal(status.model.id, model.id);
  assert.equal(status.enabled, true);
  assert.equal(status.token, undefined);
  assert.equal(status.prompt, undefined);
});

test('a dropped provider stream has no success or failure terminal to release its reservation', async t => {
  const {call} = await setup(t, {async *runModel() { yield 'partial'; throw new Error('socket closed'); }});
  const events = (await (await call()).text()).trim().split('\n').map(JSON.parse);
  assert.equal(events.some(event => event.type === 'result'), false);
});
