"""Adaptive retrieval policy for Brain queries.

Pure standard-library heuristics. This module performs no network calls, no
model calls and no database access; it only decides *how* a caller should
query the existing SQLite/FTS5 Brain.

The values produced here (``coverage``, ``level``) are routing heuristics --
rank-style signals over lexical overlap. They are NOT calibrated confidence
probabilities and must not be reported as such. Nothing in this module learns
or infers semantics; ``use_semantic`` is only a *request* that the caller may
ignore if no semantic backend is configured.

Supplied query/row text is treated as data only, never as instructions.
"""

from __future__ import annotations

import json
import re

MAX_TERMS = 24
MAX_ROWS = 60
MAX_HOPS = 2

STRATEGIES = ("auto", "keyword", "graph", "semantic")

# Characters that mark a token as a likely identifier / path / dotted name.
_SEP_CHARS = "._-/\\"

_TOKEN_RE = re.compile(r"\w+(?:[.\-/\\]\w+)*", re.UNICODE)
_SPLIT_RE = re.compile(r"[.\-/\\]")
_QUOTED_RE = re.compile(r"[\"'`]([^\"'`]+)[\"'`]")

# Question words, fillers and generic nouns that carry no retrieval signal.
STOP_WORDS = frozenset({
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "by", "can",
    "could", "did", "do", "does", "file", "files", "find", "for", "from",
    "get", "give", "how", "i", "in", "is", "it", "its", "list", "locate",
    "look", "me", "my", "need", "of", "on", "or", "our", "please", "see",
    "show", "some", "tell", "that", "the", "their", "them", "then", "there",
    "these", "this", "to", "up", "us", "want", "was", "we", "were", "what",
    "when", "where", "which", "who", "whom", "why", "will", "with", "you",
    "your",
})

# Cues that make a question *relational* rather than a plain lookup.
_RELATION_WORDS = frozenset({
    "affect", "affected", "affecting", "affects", "between", "broke",
    "broken", "breaks", "calls", "callers", "caused", "causes", "connect",
    "connected", "connects", "connection", "connections", "depend",
    "depended", "dependencies", "dependency", "depending", "depends",
    "downstream", "impact", "impacts", "link", "linked", "links", "reason",
    "related", "relation", "relations", "relationship", "relationships",
    "upstream", "used", "uses", "using", "why",
})
_RELATION_PHRASES = ("root cause", "because of", "leads to", "tied to")

def _is_identifier(token: str) -> bool:
    if (len(token)>1 and token.isupper()) or re.search(r'[a-z][A-Z]',token):
        return True
    for ch in token:
        if ch in _SEP_CHARS or ch.isdigit():
            return True
    return False


def query_terms(query):
    """Return casefolded, de-duplicated meaningful tokens (max ``MAX_TERMS``).

    Stop words are dropped unless the token looks like an identifier or path.
    If filtering removes everything, the original tokens are returned instead.
    """
    raw = _TOKEN_RE.findall(query or "")
    ordered = []
    seen = set()
    for token in raw:
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        ordered.append((folded, _is_identifier(token)))
    kept = [t for t, ident in ordered if ident or t not in STOP_WORDS]
    if not kept:
        kept = [t for t, _ in ordered]
    return kept[:MAX_TERMS]


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v is not None]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                return _as_list(json.loads(text))
            except (ValueError, TypeError):
                return [value]
        return [value] if value else []
    return [str(value)]


def _row_texts(row):
    if not isinstance(row, dict):
        return []
    parts = []
    for key in ("title", "content"):
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif value is not None:
            parts.append(str(value))
    parts.extend(_as_list(row.get("tags")))
    parts.extend(_as_list(row.get("episode")))
    return parts


def _normalize(text):
    return " ".join(t.casefold() for t in _TOKEN_RE.findall(text or ""))


def _row_tokens(texts):
    tokens = set()
    for text in texts:
        for token in _TOKEN_RE.findall(text):
            folded = token.casefold()
            tokens.add(folded)
            # whole path/dotted components count, arbitrary substrings do not
            for part in _SPLIT_RE.split(folded):
                if part:
                    tokens.add(part)
    return tokens


def _looks_like_path(term):
    if "/" in term or "\\" in term:
        return True
    head, _, tail = term.rpartition(".")
    return bool(head) and bool(tail) and tail.isalnum()


def _row_id(row):
    value = row.get("id") if isinstance(row, dict) else None
    return None if value is None else str(value)


def match_quality(query, rows):
    """Classify lexical match strength of ``rows`` against ``query``.

    Returns ``{"level", "coverage", "matched_id"}``. ``coverage`` is the best
    single-row fraction of meaningful query terms found as whole tokens --
    a routing heuristic, not a probability. Rows are never mutated.
    """
    terms = query_terms(query)
    normalized_query = _normalize(query)
    quoted = [q.casefold().strip() for q in _QUOTED_RE.findall(query or "")]
    quoted = [q for q in quoted if q]
    literals = [t for t in terms if _looks_like_path(t)]

    best_coverage = 0.0
    best_id = None
    candidates = list(rows or [])[:MAX_ROWS]

    for row in candidates:
        if not isinstance(row, dict):
            continue
        texts = _row_texts(row)
        if not texts and not literals and not quoted:
            continue
        tokens = _row_tokens(texts)
        blob = " ".join(texts).casefold()

        if normalized_query:
            names = [row.get("title")] + _as_list(row.get("tags"))
            for name in names:
                if name is not None and _normalize(str(name)) == normalized_query:
                    return {"level": "exact", "coverage": 1.0,
                            "matched_id": _row_id(row)}
        if not terms:
            continue
        def present(term):
            return term in tokens or bool(re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',blob))
        hits = sum(1 for t in terms if present(t))
        coverage = hits / float(len(terms))
        # A filename is only an exact answer when the same row also satisfies
        # the rest of the query, e.g. production versus development config.
        if coverage == 1.0 and (quoted or literals) and all(present(t) for t in quoted + literals):
            return {"level": "exact", "coverage": 1.0, "matched_id": _row_id(row)}
        if coverage > best_coverage:
            best_coverage = coverage
            best_id = _row_id(row)

    if best_coverage >= 1.0:
        level = "strong"
    elif best_coverage > 0.0:
        level = "weak"
    else:
        level = "none"
        best_id = None
    return {"level": level, "coverage": best_coverage, "matched_id": best_id}


def is_relationship_query(query):
    """True when the query asks about links between things, not a location."""
    text = (query or "").casefold()
    for phrase in _RELATION_PHRASES:
        if phrase in text:
            return True
    return any(t in _RELATION_WORDS for t in _TOKEN_RE.findall(text))


def _clamp_hops(hops):
    return max(0, min(MAX_HOPS, hops))


def plan(query, strategy="auto", hops=None, quality=None, empty=False):
    """Decide the retrieval shape for one Brain query.

    Returns ``{"requested_strategy", "graph_hops", "use_semantic", "reason"}``.
    The plan is a request only: graph expansion may find no related nodes and
    the semantic path may be unavailable. The caller records what actually ran.
    """
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy: %r" % (strategy,))
    if hops is not None and (isinstance(hops, bool) or not isinstance(hops, int)):
        raise TypeError("hops must be None or a non-bool int")
    if quality is not None and not isinstance(quality, dict):
        raise TypeError("quality must be None or a dict")

    explicit = hops is not None
    requested_hops = _clamp_hops(hops) if explicit else None
    if strategy in ("keyword", "semantic") and explicit and requested_hops > 0:
        raise ValueError(
            "strategy %r conflicts with graph hops %r" % (strategy, hops)
        )

    level = quality.get("level") if quality else None

    def built(graph_hops, use_semantic, reason):
        return {
            "requested_strategy": strategy,
            "graph_hops": graph_hops,
            "use_semantic": use_semantic,
            "reason": reason,
        }

    if empty:
        return built(0, False, "empty result set: no expansion work to request")

    if strategy == "keyword":
        return built(0, False, "explicit keyword-only retrieval")
    if strategy == "semantic":
        return built(0, True, "explicit semantic retrieval requested")
    if strategy == "graph":
        return built(requested_hops if explicit else 1, False,
                     "explicit graph retrieval")

    if explicit:
        graph_hops = requested_hops
        reason = "auto: caller-supplied hops override (%d)" % graph_hops
    elif is_relationship_query(query):
        graph_hops = 1
        reason = "auto: relationship cue detected, 1 hop"
    else:
        graph_hops = 0
        reason = "auto: lookup-style query, keyword fast path"

    if level in ("weak", "none"):
        use_semantic = True
        reason += "; lexical match %s, semantic fallback requested" % level
    else:
        use_semantic = False
        if level in ("exact", "strong"):
            reason += "; lexical match %s, semantic unnecessary" % level
        else:
            reason += "; match quality unknown, no semantic fallback requested"
    return built(graph_hops, use_semantic, reason)
