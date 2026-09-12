"""Fixed deny gate for the supplied-text AGY worker. Never execute tool arguments."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

def log(event, name):
    try:
        path = Path(__file__).resolve().parents[1] / 'runtime/antigravity-denials.ndjson'
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'event': event,
                                     'tool': name if isinstance(name, str) and len(name) < 120 else 'unknown'}) + '\n')
    except Exception:
        pass

try:
    payload = json.load(sys.stdin)
    name = payload.get('toolCall', {}).get('name', 'unknown')
except Exception:
    name = 'unknown'
log('entered', name)
# Only the trusted synthetic probe supplies this argument; normal dispatch cannot.
if sys.argv[1:] == ['--probe-timeout']:
    time.sleep(3)
log('denied', name)
print(json.dumps({'decision': 'deny', 'reason': 'Orchestrator supplied-text worker forbids tools.'}))
