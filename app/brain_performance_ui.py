"""Readable presentation of stored performance evidence; never alters the record."""

SCRIPT = r'''
function memoryContent(memory) {
  const content = typeof memory.content === 'string' ? memory.content : '';
  const plain = () => el('div', 'detail-content', content);
  const marker = '\nPerformance timing duration usage (null = unknown; wall time includes waits; reported cost is not an invoice):\n';
  const offset = content.lastIndexOf(marker);
  if (offset < 0 || !memory.source?.performance_sha256) return plain();
  let value;
  try { value = JSON.parse(content.slice(offset + marker.length)); }
  catch (_) { return plain(); }
  if (!value || value.schema_version !== 1 || Array.isArray(value)) return plain();
  const outer = el('div');
  outer.append(el('div', 'detail-content', content.slice(0, offset)));
  const panel = section('Task time and usage');
  panel.setAttribute('aria-label', 'Task time and usage');
  panel.append(el('p', 'smalltext muted', 'Wall time includes waiting. These are measurements from this task, not a prediction.'));
  const metrics = el('dl', 'metadata');
  const format = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
  const known = v => typeof v === 'number' && Number.isFinite(v) && v >= 0;
  const number = v => known(v) ? format.format(v) : 'Not recorded';
  const duration = v => !known(v) ? 'Not recorded' : format.format(v) + ' seconds';
  const money = v => !known(v) ? 'Not recorded' : '$' + (v === 0 ? '0' : v < 0.000001 ? v.toPrecision(3) : new Intl.NumberFormat('en-US', { maximumFractionDigits: 8 }).format(v));
  const label = v => typeof v === 'string' && v.length <= 160 ? v : 'Not recorded';
  const models = Array.isArray(value.models) ? value.models.filter(v => typeof v === 'string').slice(0, 6) : [];
  const rows = [
    ['Worker', label(value.worker)], ['Task size', label(value.task_size)],
    ['Wall time', duration(value.wall_seconds)], ['Execution time', duration(value.execution_seconds)],
    ['Input tokens', number(value.input_tokens)], ['Output tokens', number(value.output_tokens)],
    ['Cached input', number(value.cached_input_tokens)],
    ['Reported cost', money(value.provider_reported_cost_usd)],
    ['Verified charge', money(value.verified_additional_charge_usd)],
    ['Recall time', known(value.recall_ms) ? format.format(value.recall_ms) + ' ms' : 'Not recorded'],
    ['Memories retrieved', number(value.memories_recalled)], ['Memories supplied', number(value.memories_supplied)],
    ['Jev calls', number(value.jev_calls)], ['Jev cost', money(value.jev_cost_usd)],
    ['Models', models.length ? models.join(', ') : 'Not recorded']
  ];
  for (const [name, text] of rows) metrics.append(el('dt', '', name), el('dd', '', text));
  panel.append(metrics, el('p', 'smalltext muted', 'Reported cost is not an invoice. Supplied memories were included in requested input; this does not prove the model used them.'));
  outer.append(panel);
  return outer;
}
'''
