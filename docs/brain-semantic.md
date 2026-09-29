# Optional semantic retrieval

The Brain can use an OpenAI-compatible embeddings endpoint to find approved
memories when wording differs. Keyword lookup and relationship traversal remain
available without this connector. Nothing is enabled or installed automatically.

Choose an endpoint, an embedding model, its API charges, and the exact projects
whose memory text may be transmitted before enabling it. Subscription access to
a chat model does not establish embedding API access or a zero API charge.

Copy [brain-semantic.example.json](brain-semantic.example.json) to
`runtime/brain/semantic-config.json`, replace its placeholders, set the named
environment variable in the Orchestrator process, and set `enabled` to `true` only
after making those choices. No endpoint, model, credential, or project is supplied
by default. The config stays in local runtime storage; keep the key in the named
environment variable. Authorization matches exact project IDs, not prefixes.

The endpoint must use HTTPS and cannot contain credentials, a query string, or a
fragment. Redirects are rejected. Responses must report the exact configured model
name, so configure a canonical model identifier if your service expands aliases.
The default network timeout is two seconds and the maximum is five seconds.
Transport timeouts apply to network operations; operating-system DNS resolution
can take longer. Failures do not trigger retries.

Indexing is explicit. It sends up to 100 current approved memories in one batch,
within a 1 MiB request limit. A memory's title, content, tags, and episode are sent;
canonical source files are read locally for validation and are not sent. A memory
over 32 KiB is skipped. Indexing checks the current source before transmission and
again before saving, reserves managed storage before making the request, and
releases the Brain lock while waiting for the service. Existing current embeddings
are skipped. Repeating the explicit indexing operation can process further batches
within the 1,000-row bounded scan. It never schedules a background indexing job.

The index is a derived table in the existing Brain SQLite database. It stores memory
IDs, exact project/user scope, configuration/model identity, content fingerprints,
and normalized float vectors. It stores no additional copy of memory plaintext.
Vectors remain potentially sensitive derived data and are removed by the Brain's
forget operation. A configuration change requires explicit reindexing.

Retrieval checks up to 1,000 scoped indexed memories and returns at most 60
candidates to the Brain's existing bounded result assembly. The distinct canonical
task-source verification bound remains 128 across keyword, semantic, and final
validation phases using a shared set of checked task IDs. Sources are read again
after the network request; an earlier check does not bypass revalidation. When
the bound prevents further checks, retrieval reports an incomplete scan.
A missing, empty, invalid, or wholly
stale index makes no query embedding request. Otherwise the connector sends just
the query once, compares cosine similarity, and validates the sources and content
fingerprints again before returning candidates. The default similarity threshold
is 0.65; that is a configurable starting point, not a demonstrated quality target.

Missing or invalid configuration, unapproved projects, missing credentials,
timeouts, changed sources, and invalid vectors return explicit safe status values.
No provider error body or key is displayed. Telemetry reports request attempts,
the reported model, reported input tokens when available, and elapsed time; it does
not estimate API costs or label unknown usage zero.

Tests use only synthetic memories and mocked embedding responses. They verify
scope, index/source integrity, empty-index behavior, transport failures, and
accounting. Those tests do not establish real embedding quality or a speed benefit.
Measure answer correctness, retrieval time, supplied tokens, and task completion
time on isolated dashboard executions before deciding whether to enable semantic
fallback for a project.
