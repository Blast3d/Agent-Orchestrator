# Task: repair a compact usage-summary function

Change the supplied Python module so `summarize_usage(events)` meets this contract.
Preserve the function name/signature. Use only the Python standard library. No I/O,
network, subprocesses, imports of other project files, or code executed at import.
Return the complete replacement module, not a diff. Do not alter this contract.

`events` must be a list. Every element must be a dict with all four required keys:
`provider`, `status`, `input_tokens`, `output_tokens`. Additional keys are allowed.
Validate every event before ignoring skipped events. Invalid inputs raise ValueError.

- `provider` is a string whose stripped value is nonempty. Normalize it using
  `strip().casefold()` before grouping.
- `status` is exactly one of the strings `completed`, `failed`, or `skipped`.
- Each token count is either None or a nonnegative integer. Booleans, floats,
  strings, and negative counts are invalid. Zero is a known measurement.
- Exclude skipped events. Include both completed and failed attempts because
  failures can consume tokens. A provider with only skipped events is omitted.
- Return a list of dicts sorted by normalized provider name. Each dict has exactly
  `provider`, `attempts`, `input_tokens`, `output_tokens`, `complete_measurements`.
- `attempts` counts included events. Each token total sums the reported integers
  for that field; return None when none of the included events reports that field.
- `complete_measurements` counts included events with BOTH token fields non-None.
- Never mutate any input, including when raising ValueError. An empty list returns [].

Public examples:

1. `[{'provider':' OpenAI ','status':'completed','input_tokens':0,'output_tokens':2}]`
   produces `[{'provider':'openai','attempts':1,'input_tokens':0,'output_tokens':2,'complete_measurements':1}]`.
2. `[{'provider':'xAI','status':'failed','input_tokens':None,'output_tokens':None}]`
   produces `[{'provider':'xai','attempts':1,'input_tokens':None,'output_tokens':None,'complete_measurements':0}]`.
3. A skipped event with input_tokens=-1 raises ValueError.
