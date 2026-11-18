"""Path-checking pre-tool gate for scoped AGY workers. Never execute tool arguments.

Allows only AGY's own file tools, only with known argument names, and only for
paths the shared scoped_files policy accepts inside the worktree given as argv[1].
Everything else is denied, including unknown argument names (fail closed).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scoped_files import Scope, ScopeError

PATHS = ('AbsolutePath', 'TargetFile', 'DirectoryPath', 'SearchDirectory', 'SearchPath')
# Globs are matched below the checked directory; they must not climb out of it.
GLOBS = ('Pattern', 'Includes', 'Excludes', 'Extensions')
TOOLS = {
    'view_file': ({'AbsolutePath'}, {'StartLine', 'EndLine', 'ContentOffset', 'IncludeSummaryOfOtherLines'}),
    'list_dir': ({'DirectoryPath'}, set()),
    'find_by_name': ({'SearchDirectory'}, {'Pattern', 'Excludes', 'Type', 'MaxDepth', 'Extensions',
                                           'FullPath'}),
    'grep_search': ({'SearchPath'}, {'Query', 'MatchPerLine', 'Includes', 'CaseInsensitive', 'IsRegex'}),
    'write_to_file': ({'TargetFile'}, {'CodeContent', 'EmptyFile', 'Overwrite', 'Append', 'Description',
                                       'Complexity', 'IsArtifact', 'ArtifactMetadata'}),
    'replace_file_content': ({'TargetFile'}, {'StartLine', 'EndLine', 'TargetContent', 'ReplacementContent',
                                              'AllowMultiple', 'Instruction', 'Description', 'Complexity',
                                              'CodeMarkdownLanguage', 'TargetLintErrorIds'}),
    'multi_replace_file_content': ({'TargetFile'}, {'ReplacementChunks', 'Instruction', 'Description',
                                                    'Complexity', 'CodeMarkdownLanguage', 'TargetLintErrorIds'}),
}
# AGY adds these free-text descriptions to every call (observed with agy 1.2.16).
METADATA = {'toolAction', 'toolSummary'}
WRITES = ('write_to_file', 'replace_file_content', 'multi_replace_file_content')
ARGUMENT_KEYS = ('args', 'arguments', 'parameters', 'input')


def log(record):
    try:
        path = Path(__file__).resolve().parents[1] / 'runtime/antigravity-scoped.ndjson'
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **record}) + '\n')
    except Exception:
        pass


def _glob_leaves_folder(value):
    values = value if isinstance(value, list) else [value]
    for item in values:
        if item is None:  # an omitted optional pattern
            continue
        if not isinstance(item, str):
            return True
        normalized = item.replace('\\', '/')
        if normalized.startswith('/') or ':' in normalized or '..' in normalized.split('/'):
            return True
    return False


def decide(payload, root):
    """Return (allowed, reason, details); details never contain argument values."""
    call = payload.get('toolCall') if isinstance(payload, dict) else None
    name = call.get('name') if isinstance(call, dict) else None
    if not isinstance(name, str) or name not in TOOLS:
        return False, 'Only the scoped file tools are available.', {'tool': name if isinstance(name, str) else None}
    found = [key for key in ARGUMENT_KEYS if isinstance(call.get(key), dict)]
    if len(found) != 1:
        return False, 'Tool arguments were not recognised.', {'tool': name, 'call_keys': sorted(call)[:20]}
    arguments = call[found[0]]
    required, optional = TOOLS[name]
    keys = sorted(arguments)
    unknown = [key for key in keys if key not in required | optional | METADATA]
    if unknown or not required <= set(keys):
        return False, 'Tool arguments were not recognised.', {'tool': name, 'keys': keys[:30]}
    scope = Scope(root, read_only=False)
    try:
        for key in keys:
            if key in PATHS:
                if not isinstance(arguments[key], str):
                    raise ScopeError('Denied: invalid path.')
                scope.check_path(arguments[key], for_write=name in WRITES)
            elif key in GLOBS and _glob_leaves_folder(arguments[key]):
                raise ScopeError('Denied: patterns must stay inside the assigned folder.')
    except ScopeError as exc:
        return False, str(exc), {'tool': name, 'keys': keys}
    return True, 'Scoped file tool inside the assigned folder.', {'tool': name, 'keys': keys}


def main():
    try:
        payload = json.load(sys.stdin)
        allowed, reason, details = decide(payload, sys.argv[1])
    except Exception:
        allowed, reason, details = False, 'The scoped tool gate failed closed.', {}
    log({'decision': 'allow' if allowed else 'deny', **details})
    print(json.dumps({'decision': 'allow' if allowed else 'deny', 'reason': reason}))


if __name__ == '__main__':
    main()
