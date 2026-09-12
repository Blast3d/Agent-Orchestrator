'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);

async function monitorCommand(vscode, args) {
  let root = vscode.workspace.getConfiguration('agentOrchestrator').get('applicationRoot', '');
  if (!root) {
    try {
      const registry = JSON.parse(await fs.readFile(path.join(os.homedir(), '.codex', 'model-workers.json'), 'utf8'));
      root = registry.application_root || '';
    } catch (_) { /* A portable installation can be selected in user settings. */ }
  }
  if (typeof root !== 'string' || !path.isAbsolute(root)) {
    throw new Error('Set Agent Orchestrator: Application Root in VS Code user settings to your Orchestrator folder.');
  }
  const script = path.join(root, 'app', 'start_usage_monitor.py');
  await fs.access(script).catch(() => { throw new Error('The selected Orchestrator folder has no usage monitor. Check Application Root in user settings.'); });
  let python = process.platform === 'win32' ? 'python.exe' : 'python3';
  const bundled = path.join(root, 'python', process.platform === 'win32' ? 'python.exe' : 'python3');
  try { await fs.access(bundled); python = bundled; } catch (_) { /* Use installed Python. */ }
  let result;
  try {
    result = await execute(python, [script, ...args], {cwd: root, windowsHide: true, shell: false, timeout: 15000, maxBuffer: 16384});
  } catch (error) {
    let message;
    try { message = JSON.parse(error.stdout).error; } catch (_) { /* Do not echo raw terminal output. */ }
    throw new Error(message || 'The usage monitor command failed. Check Python and the Orchestrator installation.');
  }
  const state = JSON.parse(result.stdout);
  if (!['on', 'off', 'stopping'].includes(state.status)) throw new Error('The monitor returned an unsupported status.');
  return state;
}

function registerUsageMonitor(context, vscode, run = args => monitorCommand(vscode, args)) {
  let busy = false;
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 20);
  item.name = 'Agent Orchestrator usage monitor';
  item.text = '$(pulse) Usage monitor';
  item.tooltip = 'Turn Orchestrator allowance checks on or off for your coding session';
  item.command = 'orchestratorBots.usageMonitor';
  item.show();
  context.subscriptions.push(item, vscode.commands.registerCommand(item.command, async () => {
    if (busy) return;
    busy = true;
    try {
      const state = await run(['--status']);
      if (state.status === 'stopping') {
        await vscode.window.showInformationMessage('Usage monitor is stopping. The current quota check is ending.');
        return;
      }
      const on = state.status === 'on';
      const action = await vscode.window.showQuickPick([
        {label: on ? 'Turn off usage monitor' : 'Turn on usage monitor',
          description: on ? 'Stop background allowance checks' : 'Check allowances about every five minutes while coding', enabled: !on}
      ], {title: 'Usage monitor: ' + (on ? 'On' : 'Off'), placeHolder: 'Applies to this PC; Windows startup stays as configured'});
      if (!action) return;
      const changed = await run(action.enabled ? [] : ['--stop']);
      await vscode.window.showInformationMessage(changed.status === 'on'
        ? 'Usage monitor is on. Turn it off here when you finish coding.'
        : changed.status === 'stopping' ? 'Usage monitor is stopping after the current check.' : 'Usage monitor is off.');
    } catch (error) {
      await vscode.window.showErrorMessage(error.message || 'Usage monitor is unavailable.');
    } finally { busy = false; }
  }));
  // Adding the switch never runs Python, starts polling, or enables the monitor.
}

module.exports = {registerUsageMonitor, monitorCommand};
