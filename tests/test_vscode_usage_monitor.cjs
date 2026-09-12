'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {registerUsageMonitor} = require('../extensions/vscode-bots/usage-monitor');

function setup(state, pick) {
  const calls = [], messages = [], commands = new Map();
  const vscode = {StatusBarAlignment: {Left: 1}, window: {
    createStatusBarItem: () => ({show() {}, dispose() {}}),
    showQuickPick: async choices => pick ? choices[0] : undefined,
    showInformationMessage: async text => messages.push(text),
    showErrorMessage: async text => { throw Error(text); }
  }, commands: {registerCommand: (name, callback) => { commands.set(name, callback); return {dispose() {}}; }}};
  const run = async args => {
    calls.push(args);
    return args[0] === '--status' ? {status: state} : {status: args[0] === '--stop' ? 'stopping' : 'on'};
  };
  registerUsageMonitor({subscriptions: []}, vscode, run);
  return {calls, messages, click: commands.get('orchestratorBots.usageMonitor')};
}

test('extension activation does not start a command or a monitor', () => {
  assert.deepEqual(setup('off', true).calls, []);
});
test('opening and cancelling the switch only reads status', async () => {
  const ui = setup('off', false); await ui.click();
  assert.deepEqual(ui.calls, [['--status']]);
});
test('On starts explicitly and Off requests a graceful stop', async () => {
  const on = setup('off', true); await on.click();
  assert.deepEqual(on.calls, [['--status'], []]);
  assert.match(on.messages[0], /is on/);
  const off = setup('on', true); await off.click();
  assert.deepEqual(off.calls, [['--status'], ['--stop']]);
  assert.match(off.messages[0], /stopping/);
});
test('a stopping monitor cannot be restarted from the switch', async () => {
  const ui = setup('stopping', true); await ui.click();
  assert.deepEqual(ui.calls, [['--status']]);
  assert.match(ui.messages[0], /stopping/);
});
test('repeated clicks while the control is busy cannot duplicate actions', async () => {
  const ui = setup('off', true);
  await Promise.all([ui.click(), ui.click()]);
  assert.deepEqual(ui.calls, [['--status'], []]);
});
