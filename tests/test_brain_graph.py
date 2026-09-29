"""Standard-library tests for app/brain_graph.py.

The fixture builds a minimal but real SQLite schema with the fields the reader
touches, plus a fake Brain exposing ``lock`` and ``_connection()``. Bulk rows
are inserted with executemany, so a 3,500-node graph costs one statement
rather than thousands of approval or storage scans.
"""

import contextlib
import datetime
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _candidate in (ROOT, ROOT / "app"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import brain_graph


class GuardStub:
    """Records every lock acquisition the reader makes."""

    def __init__(self):
        self.locks = []

    @contextlib.contextmanager
    def file_lock(self, path):
        self.locks.append(path)
        yield path


GUARD = GuardStub()


SCHEMA = """
CREATE TABLE memories (
    rowid INTEGER PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    content TEXT,
    tags TEXT,
    status TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    source TEXT,
    review_note TEXT
);
CREATE TABLE relations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    actor TEXT
);
"""

INSERT_MEMORY = (
    "INSERT INTO memories (id, project_id, user_id, kind, title, content, tags,"
    " status, valid_from, valid_to, created_at, source, review_note)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
INSERT_RELATION = (
    "INSERT INTO relations (id, source_id, target_id, relation, valid_from,"
    " valid_to, actor) VALUES (?,?,?,?,?,?,?)"
)

STAMP = "%Y-%m-%dT%H:%M:%SZ"
BASE = datetime.datetime(2025, 6, 1, 12, 0, 0)
PAST = "2020-01-01T00:00:00Z"
OLD = "2021-01-01T00:00:00Z"
FUTURE = "2999-01-01T00:00:00Z"
MUTATION = re.compile(
    r"\b(insert|update|delete|drop|create|alter|replace|attach|vacuum|pragma)\b"
)


class FakeBrain:
    """Minimal Brain surface: a lock path and a sqlite3 connection factory."""

    def __init__(self, path):
        self.path = path
        self.lock = path + ".lock"
        self.statements = []
        self.connections = 0

    @contextlib.contextmanager
    def _connection(self):
        self.connections += 1
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(self.statements.append)
        try:
            yield conn
        finally:
            conn.set_trace_callback(None)
            conn.close()


class GraphReaderTest(unittest.TestCase):
    def setUp(self):
        guard=patch.object(brain_graph,'usage_guard',GUARD);guard.start();self.addCleanup(guard.stop)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "brain.sqlite3")
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        self.brain = FakeBrain(self.path)
        del GUARD.locks[:]

    @contextlib.contextmanager
    def writer(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_nodes(
        self,
        count,
        project="alpha",
        user="local",
        status="active",
        prefix="m",
        kind="fact",
        valid_from=PAST,
        valid_to=None,
        shift=0,
        title=None,
        start=0,
    ):
        rows = []
        for index in range(start, start + count):
            created = BASE + datetime.timedelta(seconds=shift - index)
            rows.append(
                (
                    "%s%05d" % (prefix, index),
                    project,
                    user,
                    kind,
                    title if title is not None else "Memory %d" % index,
                    "body text that must never be selected",
                    "tag",
                    status,
                    valid_from,
                    valid_to,
                    created.strftime(STAMP),
                    "evidence/source.md",
                    "review note",
                )
            )
        with self.writer() as conn:
            conn.executemany(INSERT_MEMORY, rows)
        return [row[0] for row in rows]

    def add_relations(self, edges, prefix="r"):
        rows = []
        for index, edge in enumerate(edges):
            source, target = edge[0], edge[1]
            relation = edge[2] if len(edge) > 2 else "supports"
            valid_from = edge[3] if len(edge) > 3 else PAST
            valid_to = edge[4] if len(edge) > 4 else None
            rows.append(
                (
                    "%s%05d" % (prefix, index),
                    source,
                    target,
                    relation,
                    valid_from,
                    valid_to,
                    "codex",
                )
            )
        with self.writer() as conn:
            conn.executemany(INSERT_RELATION, rows)
        return [row[0] for row in rows]

    def counts(self):
        with self.writer() as conn:
            memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        return memories, relations

    # -- nodes ---------------------------------------------------------------

    def test_default_limit_takes_three_thousand_newest_nodes(self):
        self.add_nodes(3500)
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["project_id"], "alpha")
        self.assertEqual(snapshot["user_id"], "local")
        self.assertEqual(snapshot["node_limit"], 3000)
        self.assertEqual(len(snapshot["nodes"]), 3000)
        self.assertEqual(snapshot["total_nodes"], 3500)
        self.assertTrue(snapshot["truncated"])
        ids = [node["id"] for node in snapshot["nodes"]]
        self.assertEqual(len(set(ids)), 3000)
        self.assertEqual(ids[0], "m00000")
        self.assertEqual(ids[2999], "m02999")
        self.assertEqual(snapshot["nodes"][2999]["title"], "Memory 2999")
        # far beyond a 200-record library snapshot and beyond a recall bundle
        for older in ("m00200", "m00250", "m01999", "m02500"):
            self.assertIn(older, ids)
        self.assertNotIn("m03000", ids)
        self.assertNotIn("m03499", ids)
        self.assertEqual(
            snapshot["validation_note"], "Source evidence is rechecked during recall."
        )
        self.assertTrue(snapshot["generated_at"])
        for node in snapshot["nodes"][:20]:
            self.assertEqual(set(node), {"id", "title", "kind"})

    def test_higher_explicit_limit_returns_everything_eligible(self):
        self.add_nodes(3500)
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha", limit=10000)
        self.assertEqual(snapshot["node_limit"], 10000)
        self.assertEqual(len(snapshot["nodes"]), 3500)
        self.assertEqual(snapshot["total_nodes"], 3500)
        self.assertFalse(snapshot["truncated"])
        self.assertEqual(snapshot["nodes"][-1]["id"], "m03499")
        smallest = brain_graph.graph_snapshot(self.brain, "alpha", limit=1)
        self.assertEqual([n["id"] for n in smallest["nodes"]], ["m00000"])
        self.assertTrue(smallest["truncated"])

    def test_other_scopes_never_consume_the_window(self):
        self.add_nodes(3000)
        # newer rows in another project and for another user must not displace
        # the in-scope nodes
        self.add_nodes(500, project="beta", prefix="b", shift=5000)
        self.add_nodes(500, user="other", prefix="o", shift=5000)
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        ids = [node["id"] for node in snapshot["nodes"]]
        self.assertEqual(len(ids), 3000)
        self.assertEqual(snapshot["total_nodes"], 3000)
        self.assertFalse(snapshot["truncated"])
        self.assertTrue(all(node_id.startswith("m") for node_id in ids))

    def test_status_and_validity_isolation(self):
        keep = self.add_nodes(3)
        self.add_nodes(1, status="pending", prefix="p", shift=100)
        self.add_nodes(1, status="superseded", prefix="s", shift=100)
        self.add_nodes(1, status="deleted", prefix="d", shift=100)
        self.add_nodes(1, prefix="f", valid_from=FUTURE, shift=100)
        self.add_nodes(1, prefix="e", valid_to=OLD, shift=100)
        self.add_nodes(1, prefix="c", valid_to=FUTURE, shift=100)
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        ids = [node["id"] for node in snapshot["nodes"]]
        self.assertEqual(sorted(ids), sorted(keep + ["c00000"]))
        self.assertEqual(snapshot["total_nodes"], 4)

    def test_order_is_stable_for_tied_timestamps(self):
        rows = []
        for identifier in ("m00003", "m00001", "m00002"):
            rows.append(
                (
                    identifier,
                    "alpha",
                    "local",
                    "fact",
                    identifier,
                    "body",
                    "",
                    "active",
                    PAST,
                    None,
                    "2025-06-01T12:00:00Z",
                    "src",
                    None,
                )
            )
        with self.writer() as conn:
            conn.executemany(INSERT_MEMORY, rows)
        first = brain_graph.graph_snapshot(self.brain, "alpha")
        second = brain_graph.graph_snapshot(self.brain, "alpha")
        ids = [node["id"] for node in first["nodes"]]
        self.assertEqual(ids, ["m00001", "m00002", "m00003"])
        self.assertEqual(ids, [node["id"] for node in second["nodes"]])

    def test_empty_project_returns_empty_graph(self):
        self.add_nodes(5, project="beta", prefix="b")
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        self.assertEqual(snapshot["nodes"], [])
        self.assertEqual(snapshot["relations"], [])
        self.assertEqual(snapshot["total_nodes"], 0)
        self.assertEqual(snapshot["total_relations"], 0)
        self.assertFalse(snapshot["truncated"])
        self.assertFalse(snapshot["relations_truncated"])
        self.assertEqual(snapshot["node_limit"], 3000)

    # -- limits --------------------------------------------------------------

    def test_invalid_limits_raise_without_clamping(self):
        self.add_nodes(3)
        for bad in (0, -1, -3000, 10001, 99999, True, False, 1.0, 3000.0, "3000", None, [3000]):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    brain_graph.graph_snapshot(self.brain, "alpha", limit=bad)
        for good in (1, 3000, 10000):
            with self.subTest(limit=good):
                snapshot = brain_graph.graph_snapshot(self.brain, "alpha", limit=good)
                self.assertEqual(snapshot["node_limit"], good)

    # -- relations -----------------------------------------------------------

    def test_edges_need_two_selected_current_endpoints(self):
        self.add_nodes(5)
        self.add_nodes(1, project="beta", prefix="b", shift=10)
        kept = self.add_relations(
            [
                ("m00000", "m00001", "supports"),
                ("m00001", "m00002", "depends_on", PAST, FUTURE),
            ],
            prefix="keep",
        )
        self.add_relations(
            [
                ("m00000", "m00003", "solves"),          # target beyond the window
                ("m00000", "b00000", "related_to"),      # foreign project row
                ("b00000", "m00000", "related_to"),      # foreign source row
                ("m00000", "ghost", "related_to"),       # missing endpoint
                ("m00001", "m00002", "supports", PAST, OLD),  # expired edge
                ("m00002", "m00000", "supports", FUTURE),
                ("m00002", "m00000", "supports", None),
                ("m00002", "m00000", "supersedes"),      # excluded relation type
            ],
            prefix="drop",
        )
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha", limit=3)
        self.assertEqual([n["id"] for n in snapshot["nodes"]], ["m00000", "m00001", "m00002"])
        self.assertEqual([r["id"] for r in snapshot["relations"]], sorted(kept))
        self.assertEqual(snapshot["total_relations"], 2)
        self.assertFalse(snapshot["relations_truncated"])
        for relation in snapshot["relations"]:
            self.assertEqual(
                set(relation), {"id", "source_id", "target_id", "relation"}
            )

    def test_large_node_window_keeps_all_edges_within_cap(self):
        self.add_nodes(3000)
        edges = [("m%05d" % i, "m%05d" % (i + 1)) for i in range(2999)]
        self.add_relations(edges, prefix="chain")
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        self.assertEqual(len(snapshot["nodes"]), 3000)
        self.assertEqual(len(snapshot["relations"]), 2999)
        self.assertEqual(snapshot["total_relations"], 2999)
        self.assertFalse(snapshot["relations_truncated"])

    def test_relation_cap_is_reported_not_hidden(self):
        original = brain_graph.MAX_RELATION_ROWS
        self.addCleanup(setattr, brain_graph, "MAX_RELATION_ROWS", original)
        brain_graph.MAX_RELATION_ROWS = 4
        self.add_nodes(3)
        self.add_relations(
            [
                ("m00000", "m00001"),
                ("m00001", "m00000"),
                ("m00000", "m00002"),
                ("m00002", "m00000"),
                ("m00001", "m00002"),
                ("m00002", "m00001"),
            ],
            prefix="edge",
        )
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        self.assertEqual(len(snapshot["relations"]), 4)
        self.assertEqual(snapshot["total_relations"], 6)
        self.assertTrue(snapshot["relations_truncated"])
        self.assertEqual(
            [r["id"] for r in snapshot["relations"]],
            ["edge00000", "edge00001", "edge00002", "edge00003"],
        )

    # -- safety --------------------------------------------------------------

    def test_titles_are_returned_verbatim_and_never_executed(self):
        hostile = "Robert'); DROP TABLE memories;-- <script>alert(1)</script>"
        self.add_nodes(1, title=hostile)
        before = self.counts()
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        self.assertEqual(len(snapshot["nodes"]), 1)
        self.assertEqual(snapshot["nodes"][0]["title"], hostile)
        self.assertEqual(self.counts(), before)
        with self.writer() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("memories", tables)
        self.assertIn("relations", tables)

    def test_single_locked_read_transaction_without_bodies(self):
        self.add_nodes(5)
        self.add_relations([("m00000", "m00001")], prefix="edge")
        before = self.counts()
        del self.brain.statements[:]
        snapshot = brain_graph.graph_snapshot(self.brain, "alpha")
        self.assertEqual(len(snapshot["nodes"]), 5)
        self.assertEqual(GUARD.locks, [self.brain.lock])
        self.assertEqual(self.brain.connections, 1)
        executed = " ".join(self.brain.statements).lower()
        self.assertLessEqual(executed.count("begin"), 1)
        forbidden = MUTATION.search(executed)
        self.assertIsNone(forbidden, "unexpected mutating statement: %r" % executed)
        # Compact origin locators are permitted; raw source notes are not.
        executed = executed.replace('json_valid(source)', '')
        for field in ('type', 'project_id', 'memory_id'):
            executed = executed.replace("json_extract(source,'$." + field + "')", '')
        for column in (r"\bcontent\b", r"\bsource\b", r"\breview_note\b", r"\btags\b"):
            self.assertIsNone(
                re.search(column, executed), "graph read touched %s" % column
            )
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()
