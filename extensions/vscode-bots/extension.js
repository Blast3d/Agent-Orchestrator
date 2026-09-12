'use strict';
const vscode = require('vscode');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {startBridge} = require('./bridge');

let bridge;
async function activate(context) {
  const directory = path.join(os.homedir(), '.agent-orchestrator', 'vscode-bots');
  await fs.mkdir(directory, {recursive: true});
  const token = crypto.randomBytes(32).toString('hex');
  const instance = crypto.randomUUID();
  const endpoint = path.join(directory, instance + '.json');
  let enabled = context.workspaceState.get('enabled', false);
  let selectedId = context.workspaceState.get('modelId', '');
  let selectedModel = null;
  const output = vscode.window.createOutputChannel('Agent Orchestrator Bots');
  context.subscriptions.push(output);

  const refresh = async () => {
    if (!enabled) { selectedModel = null; return; }
    const models = await vscode.lm.selectChatModels({vendor: 'copilot'});
    selectedModel = models.find(model => model.id === selectedId) || null;
  };
  const choose = async () => {
    const models = await vscode.lm.selectChatModels({vendor: 'copilot'});
    const picked = await vscode.window.showQuickPick(models.map(model => ({
      label: model.name, description: model.id, model
    })), {title: 'Choose the Copilot model for orchestration tasks',
      placeHolder: 'Requests use this model and your existing Copilot allowance'});
    if (!picked) return false;
    selectedId = picked.model.id; selectedModel = picked.model;
    await context.workspaceState.update('modelId', selectedId);
    return true;
  };
  const enable = async () => {
    if (!selectedModel && !(await choose())) return;
    enabled = true; await context.workspaceState.update('enabled', true);
    vscode.window.showInformationMessage('VS Code bots enabled. Orchestration can assign supplied-text tasks to ' + selectedModel.name + '.');
  };
  context.subscriptions.push(
    vscode.commands.registerCommand('orchestratorBots.enable', enable),
    vscode.commands.registerCommand('orchestratorBots.model', choose),
    vscode.commands.registerCommand('orchestratorBots.disable', async () => {
      enabled = false; await context.workspaceState.update('enabled', false);
      vscode.window.showInformationMessage('New VS Code bot assignments are disabled.');
    }),
    vscode.window.registerUriHandler({handleUri: async uri => { if (uri.path === '/enable') await enable(); }}),
    vscode.lm.onDidChangeChatModels(() => refresh().catch(() => { selectedModel = null; }))
  );
  await refresh().catch(() => { selectedModel = null; });
  bridge = await startBridge({token, isEnabled: () => enabled && !!selectedModel,
    getModel: () => selectedModel ? ({id: selectedModel.id, name: selectedModel.name,
      vendor: selectedModel.vendor, family: selectedModel.family, maxInputTokens: selectedModel.maxInputTokens}) : null,
    async *runModel(prompt, signal) {
      const model = selectedModel;
      const cancel = new vscode.CancellationTokenSource();
      const abort = () => cancel.cancel();
      signal.addEventListener('abort', abort, {once: true});
      try {
        if (signal.aborted) cancel.cancel();
        const messages = [vscode.LanguageModelChatMessage.User(prompt)];
        const count = await model.countTokens(messages[0], cancel.token);
        if (count >= model.maxInputTokens - 1024) throw Object.assign(new Error('brief-too-large'), {code: 'BriefTooLarge'});
        const response = await model.sendRequest(messages, {tools: [],
          justification: 'Complete a task assigned by your local Agent Orchestrator.'}, cancel.token);
        for await (const part of response.stream) {
          if (part instanceof vscode.LanguageModelTextPart) yield part.value;
          else if (part instanceof vscode.LanguageModelToolCallPart) throw new Error('unexpected-tool-call');
        }
      } catch (error) { output.appendLine('Model request ended: ' + (error.code || error.name || 'error')); throw error; }
      finally { signal.removeEventListener('abort', abort); cancel.cancel(); cancel.dispose(); }
    }
  });
  await fs.writeFile(endpoint, JSON.stringify({version: 1, instance, port: bridge.port, token, pid: process.pid}), {flag: 'wx', mode: 0o600});
  context.subscriptions.push({dispose() { bridge.close(); fs.unlink(endpoint).catch(() => {}); }});
  output.appendLine('Local bot bridge is ready. Use Enable VS Code Bots to choose a model.');
}
function deactivate() { if (bridge) bridge.close(); }
module.exports = {activate, deactivate};
