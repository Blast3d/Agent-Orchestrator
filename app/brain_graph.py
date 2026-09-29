"""Scoped, read-only graph snapshot for the Brain dashboard.

The reader answers one question: which current memories belong to a single
project/user scope, and how are they linked? It reads compact metadata only
(id, title, kind and relation endpoints), never memory bodies, sources or
review notes. It performs no migration, no write, no trace, no FTS or
embedding lookup, no source-file read and no provider call.

The graph window is independent of recall bundles and library snapshots: it is
bounded only by ``limit`` (default 3000, maximum 10000 nodes) and by the
existing global relation storage limit of 30000 edges.
"""

from __future__ import annotations

import brain_store
import usage_guard

SCHEMA_VERSION = 1
DEFAULT_NODE_LIMIT = 3000
MAX_NODE_LIMIT = 10000
MAX_RELATION_ROWS = 30000
VALIDATION_NOTE = "Source evidence is rechecked during recall."

# A memory is eligible when it is exactly in scope, active, and its validity
# period currently covers ``now``. A row without ``valid_from`` is not a dated
# reviewed record, so the comparison below leaves it out.
_NODE_SCOPE = """
    FROM memories
    WHERE project_id = ?
      AND user_id = ?
      AND status = 'active'
      AND valid_from <= ?
      AND (valid_to IS NULL OR valid_to > ?)
"""

# The chosen node set is computed once in SQL and reused by the relation
# queries, so edges are filtered and counted in the database rather than with
# per-node calls or thousands of bound placeholders.
_CHOSEN = (
    "WITH chosen AS (\n    SELECT id, title, kind, created_at, CASE WHEN json_valid(source) THEN json_extract(source,'$.type') END AS source_type, CASE WHEN json_valid(source) THEN json_extract(source,'$.project_id') END AS origin_project, CASE WHEN json_valid(source) THEN json_extract(source,'$.memory_id') END AS origin_memory"
    + _NODE_SCOPE
    + "    ORDER BY created_at DESC, id ASC\n    LIMIT ?\n)\n"
)

_COUNT_NODES = "SELECT COUNT(*)" + _NODE_SCOPE

_SELECT_NODES = _CHOSEN + """
SELECT id, title, kind, source_type, origin_project, origin_memory
FROM chosen
ORDER BY created_at DESC, id ASC
"""

_RELATION_SCOPE = """
FROM relations AS r
JOIN chosen AS s ON s.id = r.source_id
JOIN chosen AS t ON t.id = r.target_id
WHERE r.relation <> 'supersedes'
  AND r.valid_from <= ?
  AND (r.valid_to IS NULL OR r.valid_to > ?)
"""

_COUNT_RELATIONS = _CHOSEN + "SELECT COUNT(*)" + _RELATION_SCOPE

_SELECT_RELATIONS = (
    _CHOSEN
    + "SELECT r.id AS id, r.source_id AS source_id,\n"
    + "       r.target_id AS target_id, r.relation AS relation"
    + _RELATION_SCOPE
    + "ORDER BY r.id ASC\nLIMIT ?\n"
)


def _validate_limit(limit):
    """Return a usable node limit or raise ValueError. No silent clamping."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(
            "limit must be an integer between 1 and %d, got %r"
            % (MAX_NODE_LIMIT, limit)
        )
    if limit < 1 or limit > MAX_NODE_LIMIT:
        raise ValueError(
            "limit must be between 1 and %d, got %d" % (MAX_NODE_LIMIT, limit)
        )
    return limit


def graph_snapshot(brain, project_id, user_id="local", limit=DEFAULT_NODE_LIMIT):
    """Return a compact graph snapshot for one project/user scope.

    The result is JSON-ready::

        {"schema_version": 1, "project_id": ..., "user_id": ...,
         "nodes": [{"id", "title", "kind"}],
         "relations": [{"id", "source_id", "target_id", "relation"}],
         "total_nodes": int, "node_limit": int, "truncated": bool,
         "total_relations": int, "relations_truncated": bool,
         "generated_at": iso, "validation_note": str}

    ``validation_note`` states that source evidence is rechecked during recall;
    it is not a claim that this read revalidated any source proof.
    """
    node_limit = _validate_limit(limit)
    project = brain_store.scope(project_id, label="Project")
    user = brain_store.scope(user_id, label="User")
    moment = brain_store.now()

    node_params = (project, user, moment, moment)
    chosen_params = node_params + (node_limit,)
    relation_params = chosen_params + (moment, moment)

    with usage_guard.file_lock(brain.lock):
        with brain._connection() as conn:
            conn.execute("BEGIN")
            started = True
            try:
                total_nodes = conn.execute(_COUNT_NODES, node_params).fetchone()[0]
                node_rows = conn.execute(_SELECT_NODES, chosen_params).fetchall()
                total_relations = conn.execute(
                    _COUNT_RELATIONS, relation_params
                ).fetchone()[0]
                relation_rows = conn.execute(
                    _SELECT_RELATIONS, relation_params + (MAX_RELATION_ROWS,)
                ).fetchall()
            finally:
                if started and getattr(conn, "in_transaction", False):
                    conn.rollback()

    nodes = [
        {"id": row["id"], "title": row["title"], "kind": row["kind"]}
        for row in node_rows
    ]
    # SQL extracts only the origin locator, never source notes or body.
    for node, row in zip(nodes, node_rows):
        if row['source_type'] == 'memory_reference':
            node['reference'] = {'project_id': row['origin_project'], 'memory_id': row['origin_memory']}
    relations = [
        {
            "id": row["id"],
            "source_id": row["source_id"],
            "target_id": row["target_id"],
            "relation": row["relation"],
        }
        for row in relation_rows
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "user_id": user,
        "nodes": nodes,
        "relations": relations,
        "total_nodes": int(total_nodes),
        "node_limit": node_limit,
        "truncated": bool(total_nodes > node_limit),
        "total_relations": int(total_relations),
        "relations_truncated": bool(total_relations > MAX_RELATION_ROWS),
        "generated_at": moment,
        "validation_note": VALIDATION_NOTE,
    }
