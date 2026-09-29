# Maintenance note: Tern 0.3

The three processing stages were split into independent modules. `aggregate_attempts`
lives in `tern/usage/ledger.py`; `allocate_slots` lives in
`tern/dispatch/allocator.py`; `render_report` lives in `tern/reporting/summary.py`.
Their record interfaces and validation rules are documented in `docs/contracts.md`.

From the repository root, run `python tests/test_public.py` for all public cases,
or add `--role aggregation`, `--role allocation`, or `--role summary` to focus the
run. Tests use only the standard library and the cases in `tests/public_cases.json`.

An earlier small bug taught us to use `value is None` for missing measurements.
Zero tokens is measured data; `if not value` incorrectly classified it as unknown.
For example, a measured zero plus an unknown count has known sum zero and one
unknown contribution. This distinction applies to aggregation and report markers.

Reusable conventions: IDs normalize with `strip().casefold()` at ingestion and
sort by their canonical value. Reports validate canonical IDs without changing
them. Boolean values are never valid counts, weights, revisions, or capacities.
