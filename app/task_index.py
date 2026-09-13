"""Shared compact task projection for writers, repair and bounded readers."""
import json
import os


MAX_INDEX_BYTES = 64 * 1024
_EXCLUDED = ('response', 'provider_result', 'quota_before', 'quota_refresh', 'contribution_ledger')


def project_task_index(result):
    """Keep identity/status/usage intact; full supplied brief text stays canonical."""
    record = {key: value for key, value in result.items() if key not in _EXCLUDED}
    brief = record.get('brief_check')
    if isinstance(brief, dict):
        record['brief_check'] = {key: value for key, value in brief.items() if key != 'sections'}
    operating = record.get('orchestration_context')
    if isinstance(operating, dict):
        record['orchestration_context'] = {key: value for key, value in operating.items() if key != 'context'}
    return record


def task_index_bytes(record):
    """Match write_json's encoding/newlines and reject an unreadable index."""
    data = (json.dumps(record, indent=2, allow_nan=False) + '\n').replace('\n', os.linesep).encode('utf-8')
    if len(data) > MAX_INDEX_BYTES:
        raise ValueError('Projected task index exceeds 64 KiB; retain large payloads in the canonical result')
    return data
