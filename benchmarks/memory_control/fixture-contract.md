# Tern memory-control pilot fixture

Only `fixture/` is contestant-visible. `fixture-spec.json`, `memory-seeds.json`,
this document, and `grader.py` are controller material. Reference implementations
and hidden case generation exist only in the grader. No previous benchmark
answers were copied into this workload.

There are 13 public files under 20,000 bytes. Three separate repair modules expose
independent interfaces: aggregation reconciles revisioned attempts; allocation
uses capped weighted largest remainders; summary renders their union as TSV.
Every behavior tested by the grader is specified in public documentation. Thirty
public examples are stored as readable JSON, one case per line. Additional cases
exercise seeded permutations, errors, exact numeric types, input immutability,
large integer arithmetic, normalization, and formatting.

Public local command: `python tests/test_public.py [--role ROLE]` from the fixture
root. This script normally imports the three local modules, so the controller
should expose its own bounded public-test service instead of invoking arbitrary
contestant test scripts. The service can run:

`python -I -B grader.py --workspace WORKSPACE --role ROLE --public-only`

The controller must impose an execution deadline and output bound. The grader
does not launch candidate subprocesses or use network access. It parses at most
64 KiB / 12,000 AST nodes per owned module, rejects imports and module-level
execution, supplies restricted builtins, and rejects private attribute access.
These guards reduce accidental side effects; they are not an OS sandbox.
Candidate code should be trusted synthetic benchmark output.

`python -I -B grader.py --self-test` validates 280 reference cases, verifies public
case parity, detects four named intended defects per seeded module, detects input
mutation, and checks four rejected source forms. The full CLI returns structured
JSON with `passed`, `total`, `all_passed`, and per-role case details. Role-only
evaluation reads only that role's source file. The seeded fixture intentionally
fails; do not repair or expose reference implementations before trials.

The four frozen memory templates contain navigation, a public test command,
canonical-ID/integer conventions, and one previously solved zero-versus-None
subproblem. Every fact appears in the public historical note, with exact source
lines. Templates must be frozen before any cold outputs; insertion into the real
scoped Brain is the controller's responsibility after both cold trials. Fixture
preparation made no Brain writes, provider calls, or scored contestant attempts.

The requested pilot uses one solo cold, one team cold, one solo warm, and one team
warm trial. Equal discovery, source-read, public-feedback, and revision allowances
are controller responsibilities. Cold-first ordering, caching, time variation,
small sample size, and simulated repository tools limit generalization. This
fixture supplies no inference about real-world memory benefit or provider ranking.
