'use strict';
const http = require('node:http');
const crypto = require('node:crypto');

const MAX_BODY = 48 * 1024;
const MAX_ANSWER = 512 * 1024;

function startBridge({token, getModel, runModel, isEnabled}) {
  let busy = false;
  const active = new Set();
  const server = http.createServer(async (req, res) => {
    const host = `127.0.0.1:${server.address().port}`;
    const auth = req.headers.authorization || '';
    const wanted = `Bearer ${token}`;
    const reject = (status, error) => { res.writeHead(status, {'Content-Type': 'application/json'}); res.end(JSON.stringify({error})); };
    if (req.headers.host !== host || req.headers.origin || req.headers['sec-fetch-site'] ||
        !/^Bearer [a-f0-9]{64}$/.test(auth) || !crypto.timingSafeEqual(Buffer.from(auth), Buffer.from(wanted))) {
      return reject(403, 'Use the local orchestration dispatcher.');
    }
    if (req.method === 'GET' && req.url === '/status') {
      res.writeHead(200, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'});
      return res.end(JSON.stringify({service: 'vscode-orchestrator-bots', version: 1,
        enabled: isEnabled(), busy, model: getModel()}));
    }
    if (req.method !== 'POST' || req.url !== '/task') return reject(404, 'Unknown action.');
    if (!isEnabled()) return reject(409, 'Run Agent Orchestrator: Enable VS Code Bots in VS Code.');
    if (busy) return reject(409, 'The VS Code bot is already working. Retry after its current task finishes.');
    let body;
    try {
      let bytes = 0; const chunks = [];
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > MAX_BODY) return reject(413, 'Split this brief into smaller tasks.');
        chunks.push(chunk);
      }
      body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!body || typeof body.prompt !== 'string' || !body.prompt.trim() ||
          Buffer.byteLength(body.prompt) > 32 * 1024 || typeof body.model !== 'string' ||
          !Number.isInteger(body.timeout_seconds) || body.timeout_seconds < 10 || body.timeout_seconds > 1800) {
        return reject(400, 'Invalid task brief, model or deadline.');
      }
    } catch (_) { return reject(400, 'Invalid task request.'); }
    // A second request may have completed body parsing while the first started.
    if (busy) return reject(409, 'The VS Code bot is already working.');
    const model = getModel();
    if (!model || body.model !== model.id) return reject(409, 'The selected bot model changed. Start a new assignment.');
    busy = true;
    const controller = new AbortController(); active.add(controller);
    const timer = setTimeout(() => controller.abort(), body.timeout_seconds * 1000);
    res.on('close', () => controller.abort());
    res.writeHead(200, {'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store'});
    const emit = event => { if (!res.destroyed) res.write(JSON.stringify(event) + '\n'); };
    emit({type: 'system', subtype: 'init', model: model.id});
    let answer = '', size = 0;
    try {
      for await (const text of runModel(body.prompt, controller.signal)) {
        if (controller.signal.aborted) throw new Error('cancelled');
        if (typeof text !== 'string') throw new Error('unsupported-response');
        size += Buffer.byteLength(text);
        if (size > MAX_ANSWER) { controller.abort(); throw new Error('output-limit'); }
        answer += text;
        emit({type: 'stream_event', event: {delta: {type: 'text_delta', text}}});
      }
      if (controller.signal.aborted) throw new Error('cancelled');
      if (!answer.trim()) throw Object.assign(new Error('empty-answer'), {code: 'EmptyAnswer'});
      emit({type: 'result', is_error: false, result: answer, model: model.id,
        usage: null, transport: 'vscode-language-model-api', model_vendor: model.vendor});
    } catch (error) {
      // Cancellation/disconnection cannot establish remote completion. Omit the
      // terminal event so the dispatcher preserves the pending reservation.
      if (!controller.signal.aborted && ['NoPermissions', 'NotFound', 'Blocked', 'BriefTooLarge', 'EmptyAnswer'].includes(error.code)) {
        const code = error.code;
        emit({type: 'result', is_error: true, model: model.id, errors: [
          code === 'NoPermissions' ? 'Allow Agent Orchestrator Bots to use Copilot in VS Code, then start a new assignment.' :
          code === 'NotFound' ? 'The chosen model is unavailable. Choose a bot model in VS Code.' :
          code === 'Blocked' ? 'VS Code blocked the model request. Check Copilot access in VS Code.' :
          code === 'BriefTooLarge' ? 'The brief exceeds the selected model context. Split this task.' :
          'The VS Code model returned no answer.'
        ], error: {code}});
      } else controller.abort();
    } finally {
      clearTimeout(timer); active.delete(controller); busy = false; res.end();
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  server.maxHeadersCount = 20;
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({server, port: server.address().port,
      close() { for (const controller of active) controller.abort(); server.close(); server.closeAllConnections(); }}));
  });
}
module.exports = {startBridge};
