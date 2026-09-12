"""Check a brief's structure offline; a passing report does not prove quality.

Use objective/inputs/output/acceptance JSON fields, Markdown headings or anchored
``Goal: ...`` lines. Aliases include goal/outcome/purpose, context/sources,
deliverable(s)/expected output, and acceptance criteria/success criteria/checks/
definition of done. A whole brief enclosed in one explicitly JSON-labelled code
fence is JSON; headings inside other code fences never create real sections.
"""
import argparse
import json
from pathlib import Path
import re
import sys


WORKERS = ('claude', 'grok', 'gemini', 'local-chat', 'vscode-copilot')
SECTION_NAMES = {
    'objective': 'Objective',
    'inputs': 'Inputs',
    'output': 'Required output',
    'acceptance': 'Acceptance checks',
}
ALIASES = {
    'objective': ('objective', 'goal', 'outcome', 'purpose'),
    'inputs': ('inputs', 'context', 'sources'),
    'output': ('output', 'deliverable', 'deliverables', 'expected output', 'required output'),
    'acceptance': ('acceptance', 'acceptance criteria', 'success criteria', 'checks', 'definition of done'),
}
LOOKUP = {alias: section for section, aliases in ALIASES.items() for alias in aliases}
LIMITATION = 'This checks the brief structure; a reviewer must still judge clarity and correctness.'
FENCE = re.compile(r'^[ ]{0,3}(`{3,}|~{3,})([^\r\n]*)$')
HEADING = re.compile(r'^[ ]{0,3}#{1,6}[ \t]+(.+?)\s*$')
LABEL = re.compile(r'^[ ]{0,3}(.{1,64}?)[ \t]*:[ \t]*(.*)$')


def _section(label):
    label = label.strip().strip('*_').strip()
    label = re.sub(r'[\s_-]+', ' ', label).casefold().rstrip(':').strip()
    return LOOKUP.get(label)


def _useful(value):
    """Reject empty/bullet-only/placeholder-only bodies without judging meaning."""
    lines = []
    for line in value.splitlines():
        line = re.sub(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)', '', line)
        line = re.sub(r'^\s*\[[ xX]\]\s*', '', line)
        line = line.strip(' \t\r\n*_`~#[](){}<>:;.,!?-')
        if line:
            lines.append(line)
    if not lines:
        return False
    return any(not re.fullmatch(r'(?:TODO|TBD)(?:\s*[,/;|]\s*(?:TODO|TBD))*', line, re.I)
               for line in lines)


def _new_report(text):
    return {
        'schema_version': 1, 'ok': False, 'format': 'unstructured',
        'sections': {key: '' for key in SECTION_NAMES}, 'missing': [],
        'errors': [], 'warnings': [LIMITATION],
        'character_count': len(text) if isinstance(text, str) else 0,
    }


def _finish(report):
    report['missing'] = [label for key, label in SECTION_NAMES.items()
                         if not _useful(report['sections'][key])]
    report['ok'] = not report['errors'] and not report['missing']
    return report


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON fields are not allowed.')
        result[key] = value
    return result


def _invalid_json_constant(value):
    raise ValueError('JSON numeric constants must be finite.')


def _inspect_json(text, report):
    report['format'] = 'json'
    try:
        value = json.loads(text, object_pairs_hook=_json_pairs,
                           parse_constant=_invalid_json_constant)
    except (ValueError, RecursionError):
        report['errors'].append('The brief is not valid JSON; check its syntax and duplicate fields.')
        return
    if not isinstance(value, dict):
        report['errors'].append('A JSON brief must be an object containing the four brief sections.')
        return
    seen = set()
    for label, body in value.items():
        key = _section(label)
        if key is None:
            if 'Unrecognized JSON fields were ignored.' not in report['warnings']:
                report['warnings'].append('Unrecognized JSON fields were ignored.')
            continue
        if key in seen:
            report['errors'].append(f'{SECTION_NAMES[key]} appears under more than one JSON field or alias.')
            continue
        seen.add(key)
        if isinstance(body, str):
            report['sections'][key] = body.strip()
        elif isinstance(body, list):
            if any(not isinstance(item, str) or not item.strip() for item in body):
                report['errors'].append(f'{SECTION_NAMES[key]} must contain only nonempty text items.')
            else:
                report['sections'][key] = '\n'.join(item.strip() for item in body)
        else:
            report['errors'].append(f'{SECTION_NAMES[key]} must be text or a list of nonempty text items.')


def _inspect_markdown(text, report):
    bodies = {key: [] for key in SECTION_NAMES}
    current = None
    fence = None
    found = False
    for line in text.splitlines():
        marker = FENCE.fullmatch(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            elif current:
                # Code can be a useful section body, but never a new section.
                bodies[current].append(line)
            continue
        if marker:
            fence = marker[1]
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            label = re.sub(r'\s+#+\s*$', '', heading[1]).rstrip(':').strip()
            current = _section(label)
            if current:
                found = True
            continue
        labelled = LABEL.fullmatch(line)
        key = _section(labelled[1]) if labelled else None
        if key:
            current = key
            found = True
            body = labelled[2]
            # Also allow the common **Goal:** text form.
            if labelled[1].lstrip().startswith('**') and body.startswith('**'):
                body = body[2:].lstrip()
            bodies[current].append(body)
        elif current:
            bodies[current].append(line)
    report['format'] = 'markdown' if found else 'unstructured'
    report['sections'] = {key: '\n'.join(lines).strip() for key, lines in bodies.items()}
    if fence:
        report['warnings'].append('A code fence is not closed; headings inside it were ignored.')


def inspect_brief(text, worker='claude'):
    """Return a JSON-safe report without state changes, inference or execution."""
    report = _new_report(text)
    if worker not in WORKERS:
        report['errors'].append('Choose a supported worker: claude, grok, gemini, local-chat or vscode-copilot.')
    if not isinstance(text, str):
        report['errors'].append('The brief must be text.')
        return _finish(report)
    if worker == 'local-chat' and len(text) > 10000:
        report['errors'].append('Local chat briefs must contain at most 10,000 characters; split this task.')
    stripped = text.lstrip('\ufeff').strip()
    if not stripped:
        report['errors'].append('The brief is empty; add a goal, inputs, requested output and acceptance checks.')
        return _finish(report)
    lines = stripped.splitlines()
    opening = FENCE.fullmatch(lines[0])
    if opening and opening[2].strip().casefold() == 'json':
        closing = FENCE.fullmatch(lines[-1]) if len(lines) >= 2 else None
        if (not closing or closing[1][0] != opening[1][0]
                or len(closing[1]) < len(opening[1]) or closing[2].strip()):
            report['format'] = 'json'
            report['errors'].append('A fenced JSON brief must occupy the entire brief and have a closing fence.')
        else:
            _inspect_json('\n'.join(lines[1:-1]), report)
    elif stripped.startswith(('{', '[')):
        _inspect_json(stripped, report)
    else:
        _inspect_markdown(stripped, report)
    return _finish(report)


def inspect_file(path, worker='claude'):
    """Read UTF-8 (including a BOM); read failures never expose file contents."""
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except UnicodeError:
        report = _new_report('')
        report['errors'].append('The brief must be saved as valid UTF-8 text.')
        return _finish(report)
    except (OSError, ValueError, TypeError):
        report = _new_report('')
        report['errors'].append('The brief file could not be read; check that it exists and is readable.')
        return _finish(report)
    return inspect_brief(text, worker)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('brief', type=Path)
    parser.add_argument('--worker', choices=WORKERS, default='claude')
    parser.add_argument('--json', action='store_true', dest='as_json', help='Print the complete JSON report')
    parser.add_argument('--output', type=Path, help='Write a new JSON report; an existing file is never overwritten')
    args = parser.parse_args(argv)
    report = inspect_file(args.brief, args.worker)
    output_error = None
    if args.output:
        try:
            with args.output.open('x', encoding='utf-8') as handle:
                json.dump(report, handle, indent=2)
                handle.write('\n')
        except FileExistsError:
            output_error = 'The report already exists; choose a new output file.'
        except OSError:
            output_error = 'The report could not be written; check the output folder.'
    if output_error:
        report['errors'].append(output_error)
        report['ok'] = False
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print('Brief structure is ready.' if report['ok'] else 'Brief needs attention.')
        if report['missing']:
            print('Add useful content for: ' + ', '.join(report['missing']) + '.')
        for error in report['errors']:
            print(error)
        print(LIMITATION)
    return 0 if report['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
