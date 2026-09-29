# Tern dispatch usage report

Tern is a small, standard-library Python package that reconciles usage snapshots,
shares a finite dispatch capacity between providers, and renders a compact report.
Three existing functions need repair:

* `aggregate_attempts(events)` reconciles revisions before aggregating usage.
* `allocate_slots(requests, slots)` distributes capacity fairly and deterministically.
* `render_report(usage, plan)` joins both outputs into stable tab-separated text.

Find the implementation and read the public contract, historical maintenance note,
and public tests before changing your assigned function. Search the repository for
the function names or list the documentation and test directories. All behavior,
including validation, is specified in public files. The hidden evaluator only
checks that contract on additional inputs. There are no external dependencies,
network calls, clocks, or file operations in these functions.

Solo repair covers all three functions. A team worker repairs only the module
assigned to its role: aggregation, allocation, or summary. Keep the interfaces
stable. The modules are deliberately independent so their repairs merge directly.

Implementation limits: ordinary Python 3.10+ functions and builtins, no imports,
classes, decorators, async code, I/O, dynamic evaluation, private attribute access,
or global/nonlocal declarations. Helpers
in the same module are welcome. Only literal constants and function definitions
may execute at module load. Preserve caller inputs even on invalid input.
