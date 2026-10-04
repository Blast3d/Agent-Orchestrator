"""Bounded, project-scoped skill metadata and explicitly selected local context."""
import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath

MAX_MANIFEST_BYTES = 64 * 1024
MAX_SKILL_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 24 * 1024
MAX_SKILLS = 128
MAX_PACKS = 20
MAX_ROOTS = 20
MAX_SELECTED = 3
RESERVED_IDS = {'none', 'defer', 'insufficient', 'ask_lead'}
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,49}')
HASH_PATTERN = re.compile(r'[a-f0-9]{64}')


def _identifier(value, label):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value) or value in RESERVED_IDS:
        raise ValueError('Invalid ' + label + ' identifier.')
    return value


def _text(value, label, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 and char not in '\n\r\t' for char in value)):
        raise ValueError('Invalid ' + label + ' text.')
    return value.strip()


def _bounded_bytes(path, limit, label):
    try:
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise ValueError('Unable to read ' + label + '.') from exc
    if len(data) > limit:
        raise ValueError(label + ' exceeds size limit.')
    return data


def _relative(value, label, *, skill=False):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError('Invalid relative ' + label + ' path.')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts) or PurePosixPath(value).is_absolute():
        raise ValueError('Invalid relative ' + label + ' path.')
    if skill and parts[-1] != 'SKILL.md':
        raise ValueError('Skill references must name SKILL.md.')
    return parts


def _within(path, boundary, label):
    resolved = path.resolve()
    if not resolved.is_relative_to(boundary):
        raise ValueError(label + ' path escapes its configured root.')
    return resolved


def _root_path(value, root):
    home = Path.home().resolve()
    aliases = {
        'orchestrator': root,
        'openwhispr': home / 'openwhispr',
        'library': home / 'agent-skill-library',
        'civil3d': home / 'AppData/Roaming/Autodesk/C3D 2026/enu/Support/Civil3D-Scripts-master',
    }
    if not isinstance(value, str):
        raise ValueError('Invalid configured skill root.')
    if value == '~' or value.startswith('~/'):
        anchor, suffix = home, value[2:] if value != '~' else ''
    else:
        match = re.fullmatch(r'\$\{([a-z0-9-]+)\}(?:/(.*))?', value)
        if not match or match[1] not in aliases:
            raise ValueError('Skill roots require a known alias or ~/ path.')
        anchor, suffix = aliases[match[1]], match[2] or ''
    # Alias locations are canonical; a junction beneath one cannot escape it.
    anchor = anchor.absolute()
    parts = _relative(suffix, 'root') if suffix else []
    return _within(anchor.joinpath(*parts), anchor, 'Root')


def _manifest(root):
    path = _within(root / 'config' / 'skill-packs.json', root, 'Manifest')
    data = _bounded_bytes(path, MAX_MANIFEST_BYTES, 'Skill manifest')
    try:
        manifest = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid skill manifest JSON.') from exc
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1):
        raise ValueError('Unsupported skill manifest schema.')
    roots, packs, projects = (manifest.get(key) for key in ('roots', 'packs', 'projects'))
    if not isinstance(roots, dict) or not 1 <= len(roots) <= MAX_ROOTS:
        raise ValueError('Skill manifest roots exceed limits.')
    if not isinstance(packs, list) or not 1 <= len(packs) <= MAX_PACKS:
        raise ValueError('Skill manifest packs exceed limits.')
    if not isinstance(projects, dict) or not 1 <= len(projects) <= 32:
        raise ValueError('Skill manifest projects exceed limits.')
    resolved_roots = {_identifier(name, 'root'): _root_path(value, root) for name, value in roots.items()}
    pack_ids, skill_ids, references = set(), set(), 0
    for pack in packs:
        if not isinstance(pack, dict):
            raise ValueError('Skill packs must be objects.')
        identifier = _identifier(pack.get('id'), 'pack')
        if identifier in pack_ids:
            raise ValueError('Duplicate skill pack identifier.')
        pack_ids.add(identifier)
        _text(pack.get('label'), 'pack label', 100)
        _text(pack.get('description'), 'pack description', 600)
        if type(pack.get('optional', False)) is not bool:
            raise ValueError('Pack optional must be a boolean.')
        required_roots = pack.get('roots', [])
        if (not isinstance(required_roots, list) or len(required_roots) > MAX_ROOTS
                or any(not isinstance(name, str) or name not in resolved_roots for name in required_roots)):
            raise ValueError('Invalid pack roots.')
        skills = pack.get('skills')
        if not isinstance(skills, list) or len(skills) > MAX_SKILLS:
            raise ValueError('Pack skills exceed limits.')
        references += len(skills)
        if references > MAX_SKILLS:
            raise ValueError('Skill manifest references exceed limits.')
        for skill in skills:
            if not isinstance(skill, dict):
                raise ValueError('Skill references must be objects.')
            sid = _identifier(skill.get('id'), 'skill')
            if sid in skill_ids:
                raise ValueError('Duplicate skill identifier: ' + sid)
            skill_ids.add(sid)
            if skill.get('root') not in resolved_roots:
                raise ValueError('Unknown skill root.')
            _relative(skill.get('path'), 'skill', skill=True)
            if skill.get('availability', 'installed') not in ('installed', 'dormant'):
                raise ValueError('Invalid skill availability.')
            for field, limit in (('name', 100), ('description', 600)):
                if field in skill:
                    _text(skill[field], 'skill ' + field, limit)
    for project, allowed in projects.items():
        _identifier(project, 'project')
        if (not isinstance(allowed, list) or not allowed or len(allowed) > MAX_PACKS
                or any(not isinstance(pack, str) or pack not in pack_ids for pack in allowed)
                or len(set(allowed)) != len(allowed)):
            raise ValueError('Invalid project skill pack allowlist.')
    return data, manifest, resolved_roots


def _disabled_entries():
    """Read only path/enabled keys in Codex skill tables, never other settings."""
    home = Path.home().resolve()
    path = _within(home / '.codex' / 'config.toml', home, 'Skill settings')
    if not path.is_file():
        return set()
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Skill settings exceed size limit.')
    blocks, keys, active = [], [], False
    try:
        with path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                stripped = line.strip()
                if stripped.startswith('['):
                    if active:
                        blocks.append('\n'.join(keys))
                    active = stripped == '[[skills.config]]'
                    keys = []
                    if len(blocks) > 256:
                        raise ValueError('Skill disable entries exceed limits.')
                elif active and re.match(r'^(path|enabled)\s*=', stripped):
                    keys.append(stripped)
            if active:
                blocks.append('\n'.join(keys))
    except (OSError, UnicodeError) as exc:
        raise ValueError('Unable to read skill disable entries.') from exc
    disabled = set()
    for block in blocks:
        try:
            settings = tomllib.loads(block)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError('Invalid skill disable entry.') from exc
        if settings.get('enabled') is False:
            value = settings.get('path')
            if not isinstance(value, str) or not value:
                raise ValueError('Invalid disabled skill path.')
            disabled.add(str(Path(value).expanduser().resolve()).casefold())
    return disabled


def _metadata(text):
    fields = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return fields
    current = None
    for line in lines[1:160]:
        if line.strip() == '---':
            break
        if line.startswith((' ', '\t')) and current:
            fields[current] += ' ' + line.strip()
            continue
        current = None
        match = re.match(r'^(name|description):\s*(.*)$', line)
        if not match:
            continue
        key, value = match.groups()
        if value in ('>', '|', '>-', '|-'):
            fields[key], current = '', key
        elif value.startswith('"') and value.endswith('"'):
            try:
                fields[key] = json.loads(value)
            except json.JSONDecodeError:
                fields[key] = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            fields[key] = value[1:-1].replace("''", "'")
        else:
            fields[key] = value
    return fields


def _snapshot(root, project_id):
    project_id = _identifier(project_id, 'project')
    root = Path(root).resolve()
    manifest_bytes, manifest, roots = _manifest(root)
    if project_id not in manifest['projects']:
        raise ValueError('Project has no configured skill pack scope.')
    allowed = manifest['projects'][project_id]
    disabled = _disabled_entries()
    packs, dependencies, warnings, contents = [], [], [], {}
    for pack in manifest['packs']:
        if pack['id'] not in allowed:
            continue
        descriptor = {key: pack[key] for key in ('id', 'label', 'description')}
        descriptor['optional'] = pack.get('optional', False)
        descriptor['skills'] = []
        for root_name in pack.get('roots', []):
            skill_root = roots[root_name]
            present = skill_root.is_dir()
            dependencies.append({'pack_id': pack['id'], 'root': str(skill_root),
                                 'status': 'present_root' if present else 'missing_root'})
            warning = 'Missing root: ' + str(skill_root)
            if not present and warning not in warnings:
                warnings.append(warning)
        for reference in pack['skills']:
            skill_root = roots[reference['root']]
            path = _within(skill_root.joinpath(*_relative(reference['path'], 'skill', skill=True)), skill_root, 'Skill')
            dependency = {'id': reference['id'], 'pack_id': pack['id'], 'path': str(path),
                          'root': str(skill_root), 'availability': reference.get('availability', 'installed')}
            dependencies.append(dependency)
            if not skill_root.is_dir():
                dependency['status'] = 'missing_root'
                warning = 'Missing root: ' + str(skill_root)
                if warning not in warnings:
                    warnings.append(warning)
                continue
            if not path.is_file():
                dependency['status'] = 'missing_skill'
                warnings.append('Missing skill: ' + reference['id'])
                continue
            if dependency['availability'] == 'installed' and str(path).casefold() in disabled:
                dependency['status'] = 'disabled'
                warnings.append('Disabled installed skill: ' + reference['id'])
                continue
            data = _bounded_bytes(path, MAX_SKILL_BYTES, 'Skill ' + reference['id'])
            try:
                text = data.decode('utf-8-sig')
            except UnicodeError as exc:
                raise ValueError('Skill must be UTF-8 text: ' + reference['id']) from exc
            metadata = _metadata(text)
            name = reference.get('name', metadata.get('name') or reference['id'])
            description = reference.get('description', metadata.get('description'))
            name = _text(name, 'skill name', 100)
            if not isinstance(description, str):
                raise ValueError('Skill requires a reviewed description: ' + reference['id'])
            description = _text(description.strip()[:600], 'skill description', 600)
            sha256 = hashlib.sha256(data).hexdigest()
            dependency.update(status='available', sha256=sha256)
            descriptor['skills'].append({'id': reference['id'], 'name': name, 'description': description,
                                         'pack_id': pack['id'], 'path': str(path), 'sha256': sha256,
                                         'availability': dependency['availability'], 'bytes': len(data)})
            contents[reference['id']] = text
        packs.append(descriptor)
    identity = {'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'project_id': project_id, 'dependencies': dependencies}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'schema_version': 1, 'project_id': project_id, 'manifest_sha256': digest,
            'packs': packs, 'warnings': warnings}, contents


def catalog(root, project_id):
    """Return only explicitly allowed local metadata; never install or enable skills."""
    result, _ = _snapshot(root, project_id)
    return result


def load_selected(root, project_id, skill_ids, expected_manifest_sha256, expected_skill_hashes):
    """Load at most three complete skill files after fresh scope/hash validation."""
    if (not isinstance(skill_ids, list) or not 1 <= len(skill_ids) <= MAX_SELECTED
            or any(not isinstance(sid, str) for sid in skill_ids)
            or len(set(skill_ids)) != len(skill_ids)):
        raise ValueError('Select between one and three distinct skill IDs.')
    if (not isinstance(expected_manifest_sha256, str)
            or not HASH_PATTERN.fullmatch(expected_manifest_sha256)):
        raise ValueError('A valid expected manifest hash is required.')
    if (not isinstance(expected_skill_hashes, dict) or set(expected_skill_hashes) != set(skill_ids)
            or any(not isinstance(value, str) or not HASH_PATTERN.fullmatch(value)
                   for value in expected_skill_hashes.values())):
        raise ValueError('An expected hash is required for each selected skill.')
    result, contents = _snapshot(root, project_id)
    if result['manifest_sha256'] != expected_manifest_sha256:
        raise ValueError('Skill selection is stale; refresh the catalog and review again.')
    available = {skill['id']: skill for pack in result['packs'] for skill in pack['skills']}
    skills, sections = [], [
        'Task-scoped skill instructions. Referenced assets need separate scoped reads/tool permission; '
        'supplied-text workers have no tools. Current user instructions and permissions take precedence.\n'
    ]
    for sid in skill_ids:
        if sid not in available:
            raise ValueError('Selected skill is unavailable or outside the project scope: ' + sid)
        descriptor = available[sid]
        if descriptor['sha256'] != expected_skill_hashes[sid]:
            raise ValueError('Selected skill hash is stale: ' + sid)
        skills.append(descriptor)
        sections.append('\nSkill: ' + sid + '\nLocal assets: ' + str(Path(descriptor['path']).parent) + '\n' + contents[sid] + '\n')
    text = ''.join(sections)
    size = len(text.encode('utf-8'))
    if size > MAX_CONTEXT_BYTES:
        raise ValueError('Selected complete skill instructions exceed the 24 KiB context limit.')
    return {'text': text, 'skills': skills, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'bytes': size, 'manifest_sha256': result['manifest_sha256']}
