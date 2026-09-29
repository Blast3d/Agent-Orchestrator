SCRIPT = r'''
function retrievalSummary(searchResult) {
  var sr = searchResult && typeof searchResult === "object" ? searchResult : {};
  var r = sr.retrieval;
  var card = el("section", "card-top");
  card.style.display = "flex";
  card.style.flexDirection = "column";
  card.style.flexWrap = "wrap";
  card.style.gap = "8px";
  card.style.maxWidth = "100%";
  card.style.alignItems = "stretch";
  card.style.justifyContent = "flex-start";
  var heading = el("h3", "", "How this memory was found");
  heading.style.margin = "0";
  card.appendChild(heading);

  function isMissing(v) { return v === null || v === undefined; }
  function asText(v) {
    if (isMissing(v)) return "Unknown";
    return String(v);
  }
  function ms(v) {
    if (isMissing(v)) return "Unknown";
    return String(v) + " ms";
  }
  function addLine(cls, text) {
    var line = el("p", cls || "", text);
    line.style.margin = "0";
    card.appendChild(line);
  }

  if (!r || typeof r !== "object") {
    addLine("muted", "Retrieval method not recorded");
    return card;
  }

  var routeNames = {
    keyword: "Keyword search",
    graph: "Related memories",
    semantic: "Meaning search",
    hybrid: "Combined search",
    empty: "No lookup needed"
  };
  var route = r.route;
  var routeLabel = Object.prototype.hasOwnProperty.call(routeNames, route)
    ? routeNames[route]
    : (isMissing(route) ? "Unknown" : String(route));

  var top = el("div", "");
  top.style.display = "flex";
  top.style.flexWrap = "wrap";
  top.style.gap = "6px";
  top.style.alignItems = "center";
  top.appendChild(el("span", "pill", routeLabel));
  var qualityNames = { exact: "Exact match", strong: "Close match", weak: "Loose match", none: "No match" };
  if (!isMissing(r.quality) && Object.prototype.hasOwnProperty.call(qualityNames, r.quality)) {
    top.appendChild(el("span", "pill", "Keyword: " + qualityNames[r.quality]));
  }
  card.appendChild(top);
  if (typeof r.profile === "string") addLine("muted", "Task profile: " + r.profile);

  if (route === "empty") {
    addLine("", "Nothing needed a model request or related-memory lookup for this query.");
  } else if (typeof r.reason === "string" && r.reason) {
    addLine("", r.reason);
  }

  var nMem = Array.isArray(sr.results) ? sr.results.length : "Unknown";
  var charPart = isMissing(sr.context_chars) ? "Unknown" : String(sr.context_chars);
  addLine("muted", nMem + " memories supplied · " + charPart + " context characters");
  if (r.budget && typeof r.budget === "object") {
    addLine("muted", "Recall depth: " + asText(r.budget.depth) + ". Up to " + asText(r.budget.limit) +
      " memories / " + asText(r.budget.max_chars) + " characters. " + asText(r.candidate_count) + " local candidates found.");
  }

  addLine("muted", "Lookup: " + ms(sr.lookup_ms) + " · Saving receipt: " + ms(r.timings_ms && r.timings_ms.trace) + " · Total: " + ms(sr.elapsed_ms));

  var t = r.timings_ms && typeof r.timings_ms === "object" ? r.timings_ms : {};
  addLine("", "Keyword " + ms(t.lexical) + " · Related-memory stage " + ms(t.graph) + " · Meaning search " + ms(t.semantic));

  if (!isMissing(r.graph_added)) {
    addLine("muted", String(r.graph_added) + " related memories added");
  }

  var sem = r.semantic && typeof r.semantic === "object" ? r.semantic : {};
  var st = sem.status;
  if (st === "disabled" || st === "not_configured") {
    addLine("muted", "Semantic search off");
  } else if (st === "failed" || st === "unavailable") {
    addLine("", "Meaning search did not finish" + (typeof sem.reason === "string" && sem.reason ? ": " + sem.reason : "."));
  } else if (st === "empty_index") {
    addLine("muted", "Meaning search has nothing indexed yet");
  } else if (st && st !== "ok" && st !== "not_needed") {
    addLine("", "Meaning search unavailable" + (typeof sem.reason === "string" && sem.reason ? ": " + sem.reason : "."));
  }

  if (r.jev && typeof r.jev === "object") {
    var j = r.jev;
    addLine("muted", "Jev: " + asText(j.scored_candidate_count) + " of " + asText(j.candidate_count) +
      " candidates scored; " + asText(j.provider_calls) + " provider requests. These counts are separate from memories delivered.");
    addLine("", j.applied ? "Jev ranked these memories through OpenRouter." : "Jev kept the existing memory order: " + asText(j.reason));
    if (typeof j.time_limit_note === "string" && j.time_limit_note) addLine("", j.time_limit_note);
    addLine("muted", "Jev model: " + asText(j.model) + " · Time: " + ms(j.elapsed_ms) + " · Reported cost: " + (isMissing(j.cost_usd) ? "Unknown" : "$" + String(j.cost_usd)));
  }
  addLine("muted", "Context tokens: " + asText(r.context_tokens) + " · Embedding input tokens: " + asText(r.input_tokens));

  if (isMissing(r.provider_calls)) {
    if (route !== "empty") addLine("muted", "Provider request count unknown. Charges unknown.");
  } else {
    addLine("muted", String(r.provider_calls) + " provider requests measured. Charges unknown.");
  }

  var det = el("details", "");
  det.appendChild(el("summary", "", "More timing and trace details"));
  var extra = el("div", "panel-body");
  function row(label, value) {
    extra.appendChild(el("p", "muted", label + ": " + value));
  }
  row("Lookup clock", ms(sr.lookup_ms));
  row("Trace id", asText(sr.trace_id));
  row("Trace status", asText(sr.trace_status));
  row("Asked mode", asText(r.requested_strategy));
  row("Related-memory hops", asText(r.graph_hops));
  row("Keyword candidates", asText(r.lexical_candidates));
  row("Meaning-search additions", asText(r.semantic_added));
  row("Packing", ms(t.packing));
  row("Trace write", ms(t.trace));
  if (!isMissing(st)) row("Meaning-search state", asText(st));
  if (!isMissing(sem.model)) row("Meaning-search model", asText(sem.model));
  if (typeof sem.reason === "string" && sem.reason && st !== "failed" && st !== "unavailable") {
    row("Meaning-search note", sem.reason);
  }
  det.appendChild(extra);
  card.appendChild(det);
  return card;
}
'''
