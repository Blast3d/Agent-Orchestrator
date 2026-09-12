"""Forward one supplied task to the authenticated loopback VS Code model bridge."""
import argparse
import json
import sys
import urllib.error
from pathlib import Path
from vscode_bots import ENDPOINTS, endpoint, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--output-format', choices=['stream-json'], required=True)
    args = parser.parse_args()
    if not args.endpoint.resolve().is_relative_to(ENDPOINTS.resolve()):
        raise ValueError('The VS Code endpoint must be a local bridge receipt.')
    raw = sys.stdin.buffer.read(32769)
    if len(raw) > 32768:
        raise ValueError('The VS Code brief exceeds 32 KB; split it into smaller tasks.')
    data = endpoint(args.endpoint)
    try:
        response = request(data, '/task', {'prompt': raw.decode('utf-8'), 'model': args.model_id,
            'timeout_seconds': args.timeout_seconds}, timeout=args.timeout_seconds + 5)
    except urllib.error.HTTPError as error:
        # These statuses are rejected by the bridge before a model starts.
        if error.code in (400, 403, 404, 409, 413):
            print(json.dumps({'type': 'result', 'is_error': True,
                'errors': ['VS Code rejected task admission. Run the vscode-bots status command before a new assignment.']}), flush=True)
            return 1
        return 1  # Unknown completion: retain the reservation.
    with response:
        while True:
            chunk = response.read1(8192)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        # No raw exceptions, prompts, endpoint addresses, or tokens in stderr.
        print('VS Code bridge connection ended. Check the saved task before retrying.', file=sys.stderr)
        raise SystemExit(1)
