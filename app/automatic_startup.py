"""Persistent automatic-startup switch; manual runs are always available."""
import argparse
import json
from pathlib import Path

from paths import ROOT
from task_store import timestamp, write_json
from usage_guard import file_lock


def _path(root):
    return Path(ROOT if root is None else root) / 'runtime/automatic-startup.json'


def describe(root=None):
    """Read without creating state, starting a run or calling a provider."""
    state = {'enabled': False, 'manual_available': True, 'provider_calls': 0}
    try:
        saved = json.loads(_path(root).read_text(encoding='utf-8-sig'))
        if not isinstance(saved, dict) or type(saved.get('enabled')) is not bool:
            raise ValueError('Invalid automatic startup setting')
        state.update(enabled=saved['enabled'], updated_at=saved.get('updated_at'))
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, ValueError):
        state['reason'] = 'Automatic startup settings could not be read; automatic startup stays Off.'
    return state


def set_enabled(enabled, root=None):
    if type(enabled) is not bool:
        raise ValueError('Choose On or Off for automatic orchestration.')
    path = _path(root)
    with file_lock(path.with_name(path.name + '.lock')):
        write_json(path, {'enabled': enabled, 'updated_at': timestamp()})
    return describe(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'on', 'off'))
    args = parser.parse_args(argv)
    state = describe() if args.action == 'status' else set_enabled(args.action == 'on')
    print(json.dumps(state, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
