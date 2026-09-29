SCRIPT = r'''
function createBrainGraph(data, onSelect, options) {
  options = options || {};
  // Canvas colors come from the shared theme tokens, re-read on every draw so the
  // graph follows a light/dark switch; the literals are fallbacks only.
  var KIND_COLOR = {fact:"#94b5ff", preference:"#f4c878", episode:"#79dfc5", procedure:"#c6a0ed"};
  var BG = "#101722", FG = "#edf3fa", EDGE = "rgba(237,243,250,0.12)", EDGE_STRONG = "rgba(237,243,250,0.55)", ACCENT = "#79dfc5";
  function readTheme() {
    var css = getComputedStyle(document.documentElement);
    function token(name, fallback) { var value = css.getPropertyValue(name).trim(); return value || fallback; }
    BG = token("--ws-graph-bg", "#101722"); FG = token("--ws-text", "#edf3fa");
    EDGE = token("--ws-graph-edge", "rgba(237,243,250,0.12)"); EDGE_STRONG = token("--ws-graph-edge-strong", "rgba(237,243,250,0.55)");
    ACCENT = token("--ws-accent", "#79dfc5");
    KIND_COLOR = {fact: token("--ws-kind-fact", "#94b5ff"), preference: token("--ws-kind-preference", "#f4c878"),
                  episode: token("--ws-kind-episode", "#79dfc5"), procedure: token("--ws-kind-procedure", "#c6a0ed")};
  }
  readTheme();
  var nodesIn = (data && data.nodes) || [];
  var relsIn = (data && data.relations) || [];
  var nodes = [];
  var idToIdx = Object.create(null);
  var i, j, n, e, r, k;
  for (i = 0; i < nodesIn.length; i++) {
    n = nodesIn[i];
    if (!n || n.id == null) continue;
    var sid = String(n.id);
    if (idToIdx[sid] != null) continue;
    idToIdx[sid] = nodes.length;
    nodes.push({
      id: sid,
      title: n.title == null ? "" : String(n.title),
      kind: n.kind == null ? "fact" : String(n.kind),
      reference: n.reference || null,
      x: 0, y: 0, x2: 0, y2: 0, x3: 0, y3: 0, z3: 0, depth: 1
    });
  }
  var N = nodes.length;
  var edges = [];
  var adj = new Array(N);
  for (i = 0; i < N; i++) adj[i] = [];
  for (i = 0; i < relsIn.length; i++) {
    r = relsIn[i];
    if (!r) continue;
    var a = idToIdx[String(r.source_id)];
    var b = idToIdx[String(r.target_id)];
    if (a == null || b == null || a === b) continue;
    edges.push({a: a, b: b, rel: r.relation == null ? "" : String(r.relation)});
    adj[a].push(b);
    adj[b].push(a);
  }
  var E = edges.length;
  var neigh = new Array(N);
  for (i = 0; i < N; i++) {
    var seen = Object.create(null), lst = [];
    var aa = adj[i];
    for (j = 0; j < aa.length; j++) {
      k = aa[j];
      if (!seen[k]) { seen[k] = 1; lst.push(k); }
    }
    neigh[i] = lst;
  }
  var comps = [];
  var vis = new Uint8Array(N);
  for (i = 0; i < N; i++) {
    if (vis[i]) continue;
    var q = [i], h = 0;
    vis[i] = 1;
    var members = [];
    while (h < q.length) {
      var u = q[h++];
      members.push(u);
      var nb = neigh[u];
      for (j = 0; j < nb.length; j++) {
        var v = nb[j];
        if (!vis[v]) { vis[v] = 1; q.push(v); }
      }
    }
    comps.push(members);
  }
  comps.sort(function(p, q2) { return q2.length - p.length; });
  var GAP = 56;
  var worldW = GAP, worldH = GAP;
  var layoutOrder = [];
  for (i = 0; i < comps.length; i++) {
    for (j = 0; j < comps[i].length; j++) layoutOrder.push(comps[i][j]);
  }
  var goldenAngle = Math.PI * (3 - Math.sqrt(5));
  // Fill an irregular cloud, including its center. The smooth outline variation
  // and small deterministic offsets avoid a grid or concentric rings, while
  // keeping layout work linear and stable across refreshes.
  for (i = 0; i < N; i++) {
    var nd = nodes[layoutOrder[i]];
    var seed = 2166136261;
    for (j = 0; j < nd.id.length; j++) seed = Math.imul(seed ^ nd.id.charCodeAt(j), 16777619) >>> 0;
    var angle = i * goldenAngle;
    var radius = GAP * Math.sqrt((i + 0.5) / Math.PI);
    radius *= 1 + 0.12 * Math.sin(3 * angle) + 0.08 * Math.cos(5 * angle);
    nd.x2 = N === 1 ? 0 : 1.1 * radius * Math.cos(angle) + GAP * 0.18 * ((seed & 65535) / 65535 - 0.5);
    nd.y2 = N === 1 ? 0 : 0.9 * radius * Math.sin(angle) + GAP * 0.18 * ((seed >>> 16) / 65535 - 0.5);
  }
  var sphereRadius = Math.max(GAP * 2, GAP * Math.sqrt(N) / 2);
  for (i = 0; i < N; i++) {
    nd = nodes[layoutOrder[i]];
    var latitude = 1 - 2 * (i + 0.5) / N;
    var breadth = Math.sqrt(Math.max(0, 1 - latitude * latitude));
    nd.x3 = N === 1 ? 0 : sphereRadius * breadth * Math.cos(i * goldenAngle);
    nd.y3 = N === 1 ? 0 : sphereRadius * latitude;
    nd.z3 = N === 1 ? 0 : sphereRadius * breadth * Math.sin(i * goldenAngle);
  }
  var mode3D = Boolean(options.mode3D), yaw = 0, pitch = 0, drawOrder = [];
  function projectNodes() {
    var cosY = Math.cos(yaw), sinY = Math.sin(yaw), cosP = Math.cos(pitch), sinP = Math.sin(pitch);
    var cameraDistance = sphereRadius * 4.5;
    drawOrder = [];
    for (var idx = 0; idx < N; idx++) {
      var node = nodes[idx];
      if (mode3D) {
        var x = node.x3 * cosY + node.z3 * sinY;
        var z = -node.x3 * sinY + node.z3 * cosY;
        var y = node.y3 * cosP - z * sinP;
        z = node.y3 * sinP + z * cosP;
        var perspective = cameraDistance / (cameraDistance - z);
        node.x = x * perspective; node.y = y * perspective;
        node.depth = (z / sphereRadius + 1) / 2;
      } else {
        node.x = node.x2; node.y = node.y2; node.depth = 1;
      }
      drawOrder.push(idx);
    }
    if (mode3D) drawOrder.sort(function(a, b) { return nodes[a].depth - nodes[b].depth; });
  }
  projectNodes();
  var root = document.createElement("div");
  root.style.cssText = "display:flex;flex-direction:column;gap:8px;width:100%;max-width:100%;min-width:0;color:var(--ws-text);font:13px/1.4 var(--ws-font,system-ui,sans-serif);box-sizing:border-box;";
  root.dataset.graphNodeCount = String(N);
  root.dataset.graphEdgeCount = String(E);
  root.dataset.graphLayout = 'cloud';
  var help = document.createElement("p");
  help.textContent = "Pan the background, drag a node to rearrange, wheel or +/− to zoom toward the cursor, Fit map to see all. Click or Enter opens a memory. Search finds titles.";
  help.style.cssText = "margin:0;opacity:.85;word-wrap:break-word;";
  var toolbar = document.createElement("div");
  toolbar.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-width:0;";
  function mkBtn(label, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("aria-label", label);
    if (title) b.title = title;
    b.style.cssText = "background:var(--ws-surface-2);color:var(--ws-text);border:1px solid var(--ws-line-strong);border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit;";
    return b;
  }
  var btnIn = mkBtn("Zoom in", "Zoom in");
  var btnOut = mkBtn("Zoom out", "Zoom out");
  var btnFit = mkBtn("Fit map", "Fit all nodes");
  var btn3D = mkBtn("3D view", "Switch between the 2D cluster and a rotatable 3D globe");
  btn3D.setAttribute('aria-pressed', 'false');
  var btnReset = mkBtn("Reset rotation", "Restore the initial globe orientation");
  btnReset.hidden = true;
  var btnOpen = mkBtn("Open memory", "Open selected memory");
  toolbar.appendChild(btn3D); toolbar.appendChild(btnIn); toolbar.appendChild(btnOut); toolbar.appendChild(btnFit); toolbar.appendChild(btnReset); toolbar.appendChild(btnOpen);
  var searchWrap = document.createElement("div");
  searchWrap.style.cssText = "display:flex;flex-direction:column;gap:4px;min-width:0;width:100%;";
  var sl = document.createElement("label");
  sl.textContent = "Find a memory in map";
  sl.style.cssText = "font-weight:600;";
  var sinput = document.createElement("input");
  sinput.type = "search";
  sinput.setAttribute("aria-label", "Find a memory in map");
  sinput.style.cssText = "width:100%;max-width:100%;box-sizing:border-box;background:var(--ws-surface);color:var(--ws-text);border:1px solid var(--ws-line-strong);border-radius:6px;padding:6px 8px;font:inherit;";
  sl.htmlFor = "brain-graph-find";
  sinput.id = "brain-graph-find";
  var resultBox = document.createElement("div");
  resultBox.style.cssText = "display:flex;flex-direction:column;gap:4px;min-width:0;";
  var pageRow = document.createElement("div");
  pageRow.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;align-items:center;";
  var pageInfo = document.createElement("span");
  var prevPg = mkBtn("Previous matches", "Previous page of matches");
  var nextPg = mkBtn("Next matches", "Next page of matches");
  pageRow.appendChild(prevPg); pageRow.appendChild(nextPg); pageRow.appendChild(pageInfo);
  searchWrap.appendChild(sl); searchWrap.appendChild(sinput); searchWrap.appendChild(resultBox); searchWrap.appendChild(pageRow);
  var canvas = document.createElement("canvas");
  canvas.tabIndex = 0;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "Memory relationship map. Use arrow keys to move selection, Enter to open, plus and minus buttons or wheel to zoom, drag background to pan.");
  canvas.style.cssText = "width:100%;height:min(70vh,560px);min-height:240px;max-width:100%;display:block;background:var(--ws-graph-bg);border:1px solid var(--ws-line);border-radius:8px;touch-action:none;cursor:grab;box-sizing:border-box;";
  var selectionLabel=document.createElement('p');
  selectionLabel.setAttribute('role','status');selectionLabel.setAttribute('aria-live','polite');selectionLabel.style.cssText='margin:0;overflow-wrap:anywhere;';
  root.appendChild(help); root.appendChild(toolbar); root.appendChild(searchWrap); root.appendChild(canvas);root.appendChild(selectionLabel);

  // A separate handle keeps connection gestures distinct from moving nodes.
  var canConnect = typeof options.onCreateRelation === 'function';
  var canvasWrap = document.createElement('div');
  canvasWrap.style.cssText = 'position:relative;min-width:0;';
  root.insertBefore(canvasWrap, canvas); canvasWrap.appendChild(canvas);
  var handle = mkBtn('+', 'Drag a wire to another memory, or click to choose a connection');
  handle.setAttribute('aria-label', 'Connect selected memory');
  handle.style.cssText += 'position:absolute;width:28px;height:28px;padding:0;border-radius:50%;border:2px solid var(--ws-accent);background:var(--ws-accent-soft);touch-action:none;z-index:1;';
  handle.hidden = !canConnect; canvasWrap.appendChild(handle);
  var btnConnect = mkBtn('Connect memory', 'Choose another memory or drag the selected node connection handle');
  btnConnect.hidden = !canConnect; toolbar.appendChild(btnConnect);
  var connectionEditor = document.createElement('section');
  connectionEditor.hidden = true;
  connectionEditor.setAttribute('aria-label', 'Connect memories');
  connectionEditor.style.cssText = 'display:grid;gap:9px;padding:14px;border:1px solid var(--ws-line-strong);border-radius:8px;min-width:0;';
  root.insertBefore(connectionEditor, canvasWrap);
  var sourceLabel = document.createElement('strong'); sourceLabel.style.overflowWrap='anywhere'; connectionEditor.appendChild(sourceLabel);
  function editorField(label, tag) {
    var wrap = document.createElement('label'); wrap.textContent = label;
    wrap.style.cssText = 'display:grid;gap:4px;min-width:0;';
    var input = document.createElement(tag); input.setAttribute('aria-label', label);
    input.style.cssText = 'width:100%;min-width:0;max-width:100%;box-sizing:border-box;background:var(--ws-surface);color:' + FG + ';border:1px solid var(--ws-line-strong);border-radius:6px;padding:7px;font:inherit;';
    wrap.appendChild(input); connectionEditor.appendChild(wrap); return input;
  }
  var linkProject = editorField('Connection project', 'select');
  var linkSearch = editorField('Find connection target', 'input'); linkSearch.type = 'search';
  var linkTarget = editorField('Target memory', 'select');
  var targetCaption = document.createElement('small'); connectionEditor.appendChild(targetCaption);
  var linkRelation = editorField('Relationship', 'select');
  [['related_to','Related to'],['supports','Supports'],['depends_on','Depends on'],['solves','Solves']].forEach(function(pair) {
    var option = document.createElement('option'); option.value = pair[0]; option.textContent = pair[1]; linkRelation.appendChild(option);
  });
  var linkActor = editorField('Your name', 'input'); linkActor.value = 'Local user'; linkActor.maxLength = 100;
  var reuseHelp = document.createElement('p'); reuseHelp.style.cssText = 'margin:0;overflow-wrap:anywhere;'; connectionEditor.appendChild(reuseHelp);
  var linkNote = editorField('Why reuse this memory?', 'textarea'); linkNote.maxLength = 1000;
  var actionRow = document.createElement('div'); actionRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';
  var linkSave = mkBtn('Save connection'), linkCancel = mkBtn('Cancel connection');
  actionRow.append(linkSave, linkCancel); connectionEditor.appendChild(actionRow);
  var linkStatus = document.createElement('p'); linkStatus.style.cssText = 'margin:0;overflow-wrap:anywhere;'; linkStatus.setAttribute('role', 'status'); linkStatus.setAttribute('aria-live', 'polite'); root.insertBefore(linkStatus, selectionLabel);
  var linkSource = -1, wireSource = -1, wireX = 0, wireY = 0, wireMoved = false;
  var targetNodes = [], targetTotal = 0, targetLoad = null, targetEpoch = 0, writeRequest = null, saving = false;
  var currentProject = String(data.project_id || options.projectId || '');
  var projects = Array.from(new Set([currentProject].concat(options.projects || []))).filter(Boolean).sort();
  projects.forEach(function(project) { var option = document.createElement('option'); option.value = project; option.textContent = project; linkProject.appendChild(option); });
  linkProject.value = currentProject;
  function connectionMessage(message, error) { if (destroyed) return; linkStatus.textContent = message; linkStatus.style.color = error ? 'var(--ws-bad)' : 'var(--ws-accent)'; }
  function connectionBusy(value) {
    saving = value;
    [linkProject, linkSearch, linkTarget, linkRelation, linkActor, linkNote, linkSave].forEach(function(input) { input.disabled = value; });
    // An in-flight write may already have committed; cancellation cannot undo it.
    linkCancel.disabled = value;
    btnConnect.disabled = value || selected < 0; handle.disabled = value;
  }
  function cancelConnection() {
    wireSource = -1; ++targetEpoch;
    if (targetLoad) targetLoad.abort(); targetLoad = null;
    if (!saving) { linkSource = -1; connectionEditor.hidden = true; }
    requestDraw();
  }
  function renderTargets(wanted) {
    var query = linkSearch.value.trim().toLowerCase();
    var sourceId = linkSource >= 0 ? nodes[linkSource].id : '';
    var filtered = targetNodes.filter(function(node) { return !(linkProject.value !== currentProject && node.reference) && node.id !== sourceId && (!query || String(node.title || '').toLowerCase().includes(query) || String(node.id).toLowerCase().includes(query)); });
    var shown = filtered.slice(0, 100);
    var placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Choose a memory';
    linkTarget.replaceChildren(placeholder);
    shown.forEach(function(node) { var option = document.createElement('option'); option.value = node.id; option.textContent = node.title || node.id; linkTarget.appendChild(option); });
    if (wanted && shown.some(function(node) { return node.id === wanted; })) linkTarget.value = wanted;
    targetCaption.textContent = shown.length + ' of ' + filtered.length + ' matching titles loaded' + (targetTotal > targetNodes.length ? ' (' + targetNodes.length + ' of ' + targetTotal + ' project memories; increase the map limit to browse more)' : '') + '. Search narrows this list.' + (linkProject.value !== currentProject ? ' Linked copies are excluded; select their original project.' : '');
  }
  async function loadTargets(wanted) {
    var epoch = ++targetEpoch, project = linkProject.value;
    if (targetLoad) targetLoad.abort(); targetLoad = new AbortController();
    targetNodes = []; targetTotal = 0; renderTargets(); linkSave.disabled = true;
    var foreign = project !== currentProject;
    linkNote.parentElement.hidden = !foreign;
    reuseHelp.textContent = foreign
      ? 'Reuse only the selected memory in ' + currentProject + '. It becomes available to this project’s bot recall and configured providers, bound to the original evidence. Forgetting or replacing the original stops its recall here.'
      : 'Connect two reviewed memories in this project. The relationship guides recall; it does not change either memory.';
    linkSave.textContent = foreign ? 'Reuse and connect memory' : 'Save connection';
    linkSave.setAttribute('aria-label',linkSave.textContent);
    try {
      if (foreign) {
        if (typeof options.onLoadProjectGraph !== 'function') throw new Error('Other project browsing is unavailable.');
        targetCaption.textContent = 'Loading project titles…';
        var reply = await options.onLoadProjectGraph(project, {signal:targetLoad.signal});
        if (destroyed || epoch !== targetEpoch || project !== linkProject.value) return;
        if (!reply || reply.project_id !== project || !Array.isArray(reply.nodes)) throw new Error('The target response does not match the selected project.');
        targetNodes = reply.nodes; targetTotal = Number(reply.total_nodes) || targetNodes.length;
      } else { targetNodes = nodes; targetTotal = nodes.length; }
      if (destroyed || epoch !== targetEpoch) return;
      renderTargets(wanted); linkSave.disabled = false;
    } catch (error) {
      if (!destroyed && epoch === targetEpoch && error.name !== 'AbortError') connectionMessage('Could not load targets: ' + error.message, true);
    }
  }
  function openConnection(source, target) {
    if (!canConnect || source < 0 || saving || destroyed) return;
    linkSource = source; wireSource = -1; connectionEditor.hidden = false;
    sourceLabel.textContent = 'From: ' + (nodes[source].title || nodes[source].id);
    linkProject.value = currentProject; linkSearch.value = ''; linkNote.value = '';
    connectionMessage('Choose a relationship and save to connect these memories.');
    loadTargets(target >= 0 ? nodes[target].id : '');
    linkRelation.focus({preventScroll:true}); requestDraw();
  }
  function addSavedConnection(sourceId, targetId, relation, reference) {
    var source = idToIdx[sourceId], target = idToIdx[targetId];
    if (reference && reference.id && target == null) {
      target = N++; idToIdx[String(reference.id)] = target;
      var origin = nodes[source];
      nodes.push({id:String(reference.id),title:String(reference.title || 'Linked project memory'),kind:String(reference.kind || 'fact'),reference:reference.reference || (reference.source?.type === 'memory_reference' ? {project_id:reference.source.project_id,memory_id:reference.source.memory_id} : null),
        x:0,y:0,x2:origin.x2+GAP,y2:origin.y2+GAP,x3:origin.x3+GAP,y3:origin.y3,z3:origin.z3,depth:1});
      adj.push([]); neigh.push([]); projectNodes();
    }
    if (source == null || target == null) return;
    if (!edges.some(function(edge) { return edge.a === source && edge.b === target && edge.rel === relation; })) {
      edges.push({a:source,b:target,rel:relation}); E++;
      adj[source].push(target); adj[target].push(source);
      if (!neigh[source].includes(target)) neigh[source].push(target);
      if (!neigh[target].includes(source)) neigh[target].push(source);
    }
    root.dataset.graphNodeCount = String(N); root.dataset.graphEdgeCount = String(E); requestDraw();
  }
  linkSave.addEventListener('click', async function() {
    if (saving || linkSource < 0 || destroyed) return;
    var sourceId = nodes[linkSource].id, targetId = linkTarget.value, project = linkProject.value;
    var foreign = project !== currentProject, relation = linkRelation.value;
    if (!targetId || !targetNodes.some(function(node) { return node.id === targetId; })) return connectionMessage('Choose a target memory.', true);
    if (!linkActor.value.trim()) return connectionMessage('Enter your name for this connection.', true);
    if (foreign && linkNote.value.trim().length < 20) return connectionMessage('Add at least 20 characters explaining why this memory belongs in this project.', true);
    if (foreign && typeof options.onCreateCrossProjectReference !== 'function') return connectionMessage('Cross-project reuse is unavailable.', true);
    writeRequest = new AbortController(); connectionBusy(true); connectionMessage('Saving connection…');
    try {
      var payload = {sourceId:sourceId,targetId:targetId,targetMemoryId:targetId,targetProjectId:project,relation:relation,actor:linkActor.value.trim(),note:linkNote.value.trim(),reuse:true};
      var callback = foreign ? options.onCreateCrossProjectReference : options.onCreateRelation;
      var result = await callback(payload, {signal:writeRequest.signal});
      if (destroyed) return;
      if (foreign && !result?.reference?.id) throw new Error('The save response omitted the linked memory. Refresh the map before retrying.');
      addSavedConnection(sourceId, foreign ? result.reference.id : targetId, relation, foreign ? result.reference : null);
      connectionEditor.hidden = true; linkSource = -1;
      connectionMessage(foreign ? 'Memory reused and connected. The original evidence stays linked; recall rechecks it.' : 'Connection saved.');
    } catch (error) {
      if (!destroyed && error.name !== 'AbortError') connectionMessage('Connection not confirmed: ' + error.message, true);
    } finally { if (!destroyed) { writeRequest = null; connectionBusy(false); requestDraw(); } }
  });
  linkProject.addEventListener('change', function() { linkSearch.value = ''; loadTargets(); });
  linkSearch.addEventListener('input', function() { renderTargets(linkTarget.value); });
  linkCancel.addEventListener('click', function() { cancelConnection(); connectionMessage('Connection cancelled.'); canvas.focus({preventScroll:true}); });
  btnConnect.addEventListener('click', function() { openConnection(selected, -1); });
  handle.addEventListener('pointerdown', function(ev) {
    if (ev.button !== 0 || saving || destroyed || selected < 0) return;
    ev.preventDefault(); ev.stopPropagation(); canvas.focus({preventScroll:true});
    canvas.setPointerCapture(ev.pointerId);
    var rect = canvas.getBoundingClientRect(); wireX = ev.clientX-rect.left; wireY = ev.clientY-rect.top;
    lastPx = wireX; lastPy = wireY; wireSource = selected; wireMoved = false; moved = false;
    connectionMessage('Drag to another memory; release to choose the relationship. Escape cancels.'); requestDraw();
  });
  // Keyboard activation has no pointer gesture and uses the same editor.
  handle.addEventListener('click', function(ev) { if (ev.detail === 0) openConnection(selected, -1); });
  function onConnectionKey(ev) {
    if (ev.key === 'Escape' && (wireSource >= 0 || !connectionEditor.hidden)) {
      ev.preventDefault(); cancelConnection();
      connectionMessage(saving ? 'Saving has already started. Refresh this project to check its result if you leave.' : 'Connection cancelled.');
    }
  }
  root.addEventListener('keydown', onConnectionKey);
  var ctx = canvas.getContext("2d");
  var fitMode=true;
  var scale = 1, tx = 0, ty = 0, dpr = 1, cssW = 1, cssH = 1;
  var hover = -1, selected = N ? 0 : -1, draggingNode = -1, panning = false;
  var rotating = false, pointerNode = -1;
  var lastPx = 0, lastPy = 0, moved = false, raf = 0, destroyed = false;
  var matchIdx = [], matchPage = 0, PAGE = 10;
  function colorOf(kind) { return KIND_COLOR[kind] || KIND_COLOR.fact; }
  function worldToScreen(x, y) { return {x: x * scale + tx, y: y * scale + ty}; }
  function screenToWorld(x, y) { return {x: (x - tx) / scale, y: (y - ty) / scale}; }
  function nodeR() {
    var r = Math.max(2.5, Math.min(14, 5.5 * Math.sqrt(scale)));
    if (N > 4000) r = Math.max(2.2, Math.min(r, 10));
    return r;
  }
  function hit(sx, sy) {
    var wr = screenToWorld(sx, sy);
    var r = nodeR() / scale;
    var best = -1, bd = 1e30;
    for (i = 0; i < N; i++) {
      var radiusFactor = mode3D ? 0.65 + nodes[i].depth * 0.55 : 1;
      var r2 = r * r * radiusFactor * radiusFactor * 2.2;
      var dx = nodes[i].x - wr.x, dy = nodes[i].y - wr.y;
      var d = dx * dx + dy * dy;
      var score = d / r2 - (mode3D ? nodes[i].depth * 0.25 : 0);
      if (d <= r2 && score < bd) { bd = score; best = i; }
    }
    return best;
  }
  function clampScale(s) {
    var mn = 0.02, mx = 24;
    if (N > 0) {
      mn = Math.max(0.008, Math.min(0.15, 8 / Math.max(worldW, worldH)));
      mx = Math.min(40, Math.max(8, 28 / Math.max(0.5, GAP * 0.15)));
    }
    if (!(s > 0) || !isFinite(s)) s = 1;
    return Math.min(mx, Math.max(mn, s));
  }
  function fit() {
    fitMode=true;
    if (!N) {scale=1;tx=cssW/2;ty=cssH/2;return;}
    var minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    for(var idx=0;idx<N;idx++){var node=nodes[idx];minX=Math.min(minX,node.x);minY=Math.min(minY,node.y);maxX=Math.max(maxX,node.x);maxY=Math.max(maxY,node.y);}
    // Keep the full globe in frame at every rotation, including its perspective.
    if(mode3D && N>1){minX=minY=-sphereRadius*1.1;maxX=maxY=sphereRadius*1.1;}
    worldW=Math.max(GAP,maxX-minX+GAP);worldH=Math.max(GAP,maxY-minY+GAP);
    scale=clampScale(Math.min(Math.max(1,cssW-56)/worldW,Math.max(1,cssH-56)/worldH));
    tx=cssW/2-(minX+maxX)/2*scale;ty=cssH/2-(minY+maxY)/2*scale;
  }
  function zoomAt(cx, cy, factor) {
    fitMode=false;
    var w = screenToWorld(cx, cy);
    scale = clampScale(scale * factor);
    tx = cx - w.x * scale;
    ty = cy - w.y * scale;
  }
  function focusNode(idx) {
    if (idx < 0 || idx >= N) return;
    selected = idx;fitMode=false;
    var tscale = clampScale(Math.max(scale, 3.2));
    var p = worldToScreen(nodes[idx].x, nodes[idx].y);
    scale = tscale;
    var q = worldToScreen(nodes[idx].x, nodes[idx].y);
    tx += cssW * 0.5 - q.x;
    ty += cssH * 0.45 - q.y;
    requestDraw();
  }
  function schedule(fn) {
    if (destroyed) return;
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(function() { raf = 0; fn(); });
  }
  function requestDraw() { schedule(draw); }
  function draw() {
    if (destroyed || !ctx) return;
    var w = Math.round(cssW * dpr), h = Math.round(cssH * dpr);
    if (canvas.width !== w) canvas.width = Math.max(1, w);
    if (canvas.height !== h) canvas.height = Math.max(1, h);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    readTheme();
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.save();
    ctx.translate(tx, ty);
    ctx.scale(scale, scale);
    var r = nodeR() / scale;
    var selN = selected >= 0 ? neigh[selected] : null;
    var selSet = null;
    if (selN && selN.length) {
      selSet = Object.create(null);
      for (j = 0; j < selN.length; j++) selSet[selN[j]] = 1;
    }
    ctx.lineWidth = Math.max(0.6 / scale, 0.35);
    ctx.strokeStyle = EDGE;
    ctx.beginPath();
    for (i = 0; i < E; i++) {
      e = edges[i];
      var hi = selected >= 0 && (e.a === selected || e.b === selected);
      if (hi) continue;
      ctx.moveTo(nodes[e.a].x, nodes[e.a].y);
      ctx.lineTo(nodes[e.b].x, nodes[e.b].y);
    }
    ctx.stroke();
    if (selected >= 0) {
      ctx.strokeStyle = EDGE_STRONG;
      ctx.lineWidth = Math.max(1.2 / scale, 0.6);
      ctx.beginPath();
      for (i = 0; i < E; i++) {
        e = edges[i];
        if (e.a === selected || e.b === selected) {
          ctx.moveTo(nodes[e.a].x, nodes[e.a].y);
          ctx.lineTo(nodes[e.b].x, nodes[e.b].y);
        }
      }
      ctx.stroke();
    }
    var visible=[];
    for(var vi=0;vi<N;vi++){var vp=worldToScreen(nodes[vi].x,nodes[vi].y);if(vp.x>=0&&vp.y>=0&&vp.x<=cssW&&vp.y<=cssH)visible.push(vi);}
    var showAllLabels=scale>=2.6&&visible.length<=100;
    var labelMin = 2.6;
    for (var drawIdx = 0; drawIdx < N; drawIdx++) {
      i = drawOrder[drawIdx]; n = nodes[i];
      var renderedRadius = r * (mode3D ? 0.65 + n.depth * 0.55 : 1);
      ctx.beginPath();
      ctx.arc(n.x, n.y, renderedRadius, 0, Math.PI * 2);
      ctx.fillStyle = colorOf(n.kind);
      if (selected === i) ctx.globalAlpha = 1;
      else if (hover === i) ctx.globalAlpha = 0.95;
      else if (selSet && selSet[i]) ctx.globalAlpha = 0.9;
      else ctx.globalAlpha = mode3D ? 0.25 + n.depth * 0.65 : 0.82;
      ctx.fill();
      if (selected === i) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = FG;
        ctx.lineWidth = Math.max(1.4 / scale, 0.5);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = FG;
    ctx.font = Math.max(11 / scale, 0.9) + "px system-ui,sans-serif";
    ctx.textBaseline = "top";
    function drawLabel(idx, always) {
      if (idx < 0) return;
      n = nodes[idx];
      var fs = Math.max(11 / scale, 0.85);
      if (!always && scale < labelMin) return;
      ctx.font = fs + "px system-ui,sans-serif";
      ctx.fillStyle = FG;
      var t = n.title || n.id;
      if (t.length > 48) t = t.slice(0, 47) + "…";
      ctx.fillText(t, n.x + r + 3 / scale, n.y - fs * 0.4);
    }
    if (showAllLabels) {
      for (i = 0; i < visible.length; i++) drawLabel(visible[i], false);
    } else {
      drawLabel(hover, true);
      if (selected !== hover) drawLabel(selected, true);
    }
    ctx.restore();
    if (canConnect && selected >= 0) {
      var hp = worldToScreen(nodes[selected].x, nodes[selected].y);
      var handleX = hp.x + nodeR() + 7, handleY = hp.y - 42;
      handle.style.left = handleX + 'px'; handle.style.top = handleY + 'px';
      handle.hidden = hp.x < 0 || handleY < 0 || handleX + 28 > cssW || hp.y > cssH;
      if(!handle.hidden){ctx.save();ctx.strokeStyle=ACCENT;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(hp.x,hp.y);ctx.lineTo(handleX+14,handleY+14);ctx.stroke();ctx.restore();}
      handle.dataset.sourceId = nodes[selected].id;
      if (wireSource >= 0) {
        var wp = worldToScreen(nodes[wireSource].x, nodes[wireSource].y);
        ctx.save(); ctx.strokeStyle = ACCENT; ctx.lineWidth = 2; ctx.setLineDash([6,4]);
        ctx.beginPath(); ctx.moveTo(wp.x,wp.y); ctx.lineTo(wireX,wireY); ctx.stroke(); ctx.restore();
      }
    } else handle.hidden = true;
    btnConnect.disabled = selected < 0 || saving;
    canvas.dataset.selectedMemoryId = selected >= 0 ? nodes[selected].id : '';
    canvas.dataset.drawnNodeCount = String(N);
    canvas.dataset.visibleNodeCount=String(visible.length);canvas.dataset.graphScale=String(scale);
    canvas.dataset.graphMode=mode3D?'3d':'2d';canvas.dataset.cameraYaw=String(yaw);canvas.dataset.cameraPitch=String(pitch);
    var active=hover>=0?hover:selected;var label=active>=0?nodes[active].title:'No memories in this project.';
    if(active>=0&&nodes[active].reference?.project_id)label+=' ? Linked from '+nodes[active].reference.project_id;
    if(selectionLabel.textContent!==label)selectionLabel.textContent=label;
    btnOpen.disabled=selected<0;
  }
  var fitted=false;
  function resize() {
    if (destroyed) return;
    var rect = canvas.getBoundingClientRect();
    var oldW=cssW,oldH=cssH;
    cssW = Math.max(1, rect.width);
    cssH = Math.max(1, rect.height);
    dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    if(rect.width>1 && (!fitted||fitMode)){fit();fitted=true;}else if(fitted){tx+=(cssW-oldW)/2;ty+=(cssH-oldH)/2;}
    requestDraw();
  }
  var lastMoveT = 0;
  function onPointerMove(ev) {
    if (destroyed) return;
    var rect = canvas.getBoundingClientRect();
    var px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    if (wireSource >= 0) {
      wireX = px; wireY = py; hover = hit(px, py);
      if (Math.abs(px-lastPx)+Math.abs(py-lastPy) >= 4) wireMoved = true;
      canvas.style.cursor = hover >= 0 && hover !== wireSource ? 'crosshair' : 'copy';
      requestDraw(); return;
    }
    if (rotating) {
      if(!moved && Math.abs(px-lastPx)+Math.abs(py-lastPy)<4)return;
      // Wrap full turns instead of clamping at the poles. Both axes can keep
      // rotating in either direction, without accumulating very large angles.
      yaw = (yaw + (px-lastPx)*0.007) % (2*Math.PI);
      pitch = (pitch + (py-lastPy)*0.007) % (2*Math.PI);
      lastPx=px;lastPy=py;moved=true;hover=-1;
      projectNodes();requestDraw();return;
    }
    if (draggingNode >= 0) {
      if(!moved&&Math.abs(px-lastPx)+Math.abs(py-lastPy)<4)return;
      var w = screenToWorld(px, py);
      nodes[draggingNode].x = nodes[draggingNode].x2 = w.x;
      nodes[draggingNode].y = nodes[draggingNode].y2 = w.y;
      moved = true;
      requestDraw();
      return;
    }
    if (panning) {
      fitMode=false;
      tx += px - lastPx;
      ty += py - lastPy;
      lastPx = px; lastPy = py;
      moved = true;
      requestDraw();
      return;
    }
    var now = ev.timeStamp || 0;
    if (now - lastMoveT < 24) return;
    lastMoveT = now;
    var hov = hit(px, py);
    if (hov !== hover) { hover = hov; canvas.style.cursor = hov >= 0 ? "pointer" : "grab"; requestDraw(); }
  }
  function onPointerDown(ev) {
    if (destroyed || ev.button !== 0) return;
    canvas.focus({preventScroll:true});
    canvas.setPointerCapture(ev.pointerId);
    var rect = canvas.getBoundingClientRect();
    lastPx = ev.clientX - rect.left; lastPy = ev.clientY - rect.top;
    moved = false;
    var h = hit(lastPx, lastPy);
    if (mode3D && !ev.shiftKey) {
      rotating=true;pointerNode=h;if(h>=0)selected=h;canvas.style.cursor='grabbing';
    }
    else if (!mode3D && h >= 0) { draggingNode = h; selected = h; }
    else { panning = true; canvas.style.cursor = "grabbing"; }
    requestDraw();
  }
  function onPointerUp(ev) {
    if (wireSource >= 0) {
      var source = wireSource, rect = canvas.getBoundingClientRect();
      var target = hit(ev.clientX-rect.left, ev.clientY-rect.top); wireSource = -1;
      if (ev.type === 'pointerup' && target >= 0 && target !== source) openConnection(source, target);
      else if (ev.type === 'pointerup' && !wireMoved) openConnection(source, -1);
      else connectionMessage('Connection cancelled. Drop the wire onto another memory to connect.');
      canvas.style.cursor = 'grab'; requestDraw(); return;
    }
    var wasDrag = rotating ? pointerNode : draggingNode;
    var didMove = moved;
    draggingNode = -1; panning = false; rotating = false; pointerNode = -1;
    canvas.style.cursor = hover >= 0 ? "pointer" : "grab";
    if (wasDrag >= 0 && !didMove && typeof onSelect === "function") {
      selected = wasDrag;
      if(ev.type==='pointerup')onSelect(nodes[wasDrag].id);
    } else if (!didMove && wasDrag < 0) {
      var rect = canvas.getBoundingClientRect();
      var h = hit(ev.clientX - rect.left, ev.clientY - rect.top);
      if (h >= 0) selected = h;
    }
    requestDraw();
  }
  function onWheel(ev) {
    ev.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomAt(ev.clientX - rect.left, ev.clientY - rect.top, factor);
    requestDraw();
  }
  function stepSel(dx, dy) {
    if (N === 0) return;
    if (selected < 0) { selected = 0; requestDraw(); return; }
    var sx = nodes[selected].x, sy = nodes[selected].y;
    var best = -1, bd = 1e30;
    for (i = 0; i < N; i++) {
      if (i === selected) continue;
      var ox = nodes[i].x - sx, oy = nodes[i].y - sy;
      var proj = ox * dx + oy * dy;
      if (proj <= 0) continue;
      var perp = ox * dy - oy * dx;
      var d = proj + Math.abs(perp) * 0.65;
      if (d < bd) { bd = d; best = i; }
    }
    if (best >= 0) { focusNode(best); }
  }
  function onKey(ev) {
    if (destroyed) return;
    var kcode = ev.key;
    if (kcode === "ArrowLeft") { ev.preventDefault(); stepSel(-1, 0); }
    else if (kcode === "ArrowRight") { ev.preventDefault(); stepSel(1, 0); }
    else if (kcode === "ArrowUp") { ev.preventDefault(); stepSel(0, -1); }
    else if (kcode === "ArrowDown") { ev.preventDefault(); stepSel(0, 1); }
    else if (kcode === "Enter" && selected >= 0 && typeof onSelect === "function") {
      ev.preventDefault();
      onSelect(nodes[selected].id);
    } else if (kcode === "+" || kcode === "=") { ev.preventDefault(); zoomAt(cssW / 2, cssH / 2, 1.15); requestDraw(); }
    else if (kcode === "-" || kcode === "_") { ev.preventDefault(); zoomAt(cssW / 2, cssH / 2, 1 / 1.15); requestDraw(); }
  }
  function rebuildMatches() {
    var q = (sinput.value || "").toLowerCase();
    matchIdx = [];
    if (q) {
      for (i = 0; i < N; i++) {
        if (nodes[i].title.toLowerCase().indexOf(q) >= 0 || nodes[i].id.toLowerCase().indexOf(q) >= 0)
          matchIdx.push(i);
      }
    }
    matchPage = 0;
    renderMatches();
  }
  function renderMatches() {
    while (resultBox.firstChild) resultBox.removeChild(resultBox.firstChild);
    var total = matchIdx.length;
    var pages = Math.max(1, Math.ceil(total / PAGE) || 1);
    if (matchPage >= pages) matchPage = pages - 1;
    if (matchPage < 0) matchPage = 0;
    var start = matchPage * PAGE;
    var end = Math.min(total, start + PAGE);
    for (i = start; i < end; i++) {
      (function(idx) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = nodes[idx].title || nodes[idx].id;
        b.style.cssText = "text-align:left;background:var(--ws-surface-2);color:var(--ws-text);border:1px solid var(--ws-line-strong);border-radius:6px;padding:6px 8px;cursor:pointer;font:inherit;width:100%;max-width:100%;box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
        b.addEventListener("click", function() { focusNode(idx); canvas.focus(); });
        resultBox.appendChild(b);
      })(matchIdx[i]);
    }
    pageInfo.textContent = total ? ("Matches " + (start + 1) + "–" + end + " of " + total) : (sinput.value ? "No matches" : "Type to search");
    prevPg.disabled = matchPage <= 0 || !total;
    nextPg.disabled = end >= total;
  }
  btnIn.addEventListener("click", function() { zoomAt(cssW / 2, cssH / 2, 1.2); requestDraw(); });
  btnOut.addEventListener("click", function() { zoomAt(cssW / 2, cssH / 2, 1 / 1.2); requestDraw(); });
  btnFit.addEventListener("click", function() { fit(); requestDraw(); });
  function updateModeUI() {
    btn3D.setAttribute('aria-pressed',String(mode3D));
    btn3D.style.background=mode3D?'var(--ws-nav-current)':'var(--ws-surface-2)';btnReset.hidden=!mode3D;
    root.dataset.graphLayout=mode3D?'sphere':'cloud';
    help.textContent=mode3D
      ? 'Drag to spin the globe freely in any direction. Shift-drag to pan. Wheel or +/− to zoom. Click or Enter opens a memory. Turn off 3D view for the 2D cluster.'
      : 'Pan the background, drag a node to rearrange, wheel or +/− to zoom toward the cursor, Fit map to see all. Click or Enter opens a memory. Search finds titles.';
    if(canConnect)help.textContent += ' Drag the green + handle to another node, or choose Connect memory. Connections are saved after review.';
    canvas.setAttribute('aria-label',mode3D
      ? '3D memory relationship map. Drag to rotate, Shift-drag to pan. Use arrow keys to move selection, Enter to open, plus and minus buttons or wheel to zoom.'
      : 'Memory relationship map. Use arrow keys to move selection, Enter to open, plus and minus buttons or wheel to zoom, drag background to pan.');
  }
  btn3D.addEventListener('click', function() {
    wireSource=-1;mode3D=!mode3D;draggingNode=-1;panning=false;rotating=false;pointerNode=-1;hover=-1;
    updateModeUI();projectNodes();fit();requestDraw();
    if(typeof options.onModeChange==='function')options.onModeChange(mode3D);
  });
  btnReset.addEventListener('click', function() { yaw=0;pitch=0;projectNodes();fit();requestDraw(); });
  btnOpen.addEventListener("click", function() {
    if (selected >= 0 && typeof onSelect === "function") onSelect(nodes[selected].id);
  });
  sinput.addEventListener("input", rebuildMatches);
  prevPg.addEventListener("click", function() { matchPage--; renderMatches(); });
  nextPg.addEventListener("click", function() { matchPage++; renderMatches(); });
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerUp);
  canvas.addEventListener("wheel", onWheel, {passive: false});
  canvas.addEventListener("keydown", onKey);
  var ro = new ResizeObserver(function() { resize(); });
  ro.observe(canvas);
  function onWinBlur() { wireSource = -1; requestDraw(); draggingNode = -1; panning = false; rotating = false; pointerNode = -1; }
  window.addEventListener("blur", onWinBlur);
  // Redraw with the other palette when the system switches between light and dark.
  var schemeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function onScheme() { requestDraw(); }
  if (schemeQuery && schemeQuery.addEventListener) schemeQuery.addEventListener("change", onScheme);
  updateModeUI();
  resize();
  fit();
  draw();
  rebuildMatches();
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    ++targetEpoch; if(targetLoad)targetLoad.abort(); if(writeRequest)writeRequest.abort();
    root.removeEventListener("keydown",onConnectionKey);
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    ro.disconnect();
    window.removeEventListener("blur", onWinBlur);
    if (schemeQuery && schemeQuery.removeEventListener) schemeQuery.removeEventListener("change", onScheme);
    canvas.removeEventListener("pointerdown", onPointerDown);
    canvas.removeEventListener("pointermove", onPointerMove);
    canvas.removeEventListener("pointerup", onPointerUp);
    canvas.removeEventListener("pointercancel", onPointerUp);
    canvas.removeEventListener("wheel", onWheel);
    canvas.removeEventListener("keydown", onKey);
  }
  return {element: root, destroy: destroy};
}
'''
