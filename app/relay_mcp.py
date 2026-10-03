"""Dependency-free stdio MCP server that lets the Relay dot read Orchestrator state and message the lead.

Register it in ~/.codex/config.toml so local Codex threads (which a connected dot
can create and control) see the tools. Every tool is local, makes no model
calls, and grants no dispatch, review, checkpoint or leadership authority.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback

import relay_bridge

SERVER = {'name': 'agent-orchestrator-relay', 'title': 'Agent Orchestrator for Relay', 'version': '0.2.0'}
PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = ('Bridge between the Relay dot and the local Agent Orchestrator. Start with relay_handoff, '
                'then relay_read_messages. Post questions, requests and handoff notes with relay_post_to_lead; '
                'the recorded lead of each run decides and replies. Answer Jacob\'s voice messages with '
                'relay_reply_to_user. Preserve a unique client_ref per write across retries, with unchanged content. '
                'Reads peek; acknowledge received IDs with relay_ack_messages only after receipt. These tools never start workers, accept '
                'reviews, change checkpoints or transfer leadership.')
RUN_ID = {'type': 'string', 'description': 'Exact run folder name, e.g. relay-dot-bridge-20261002T022539Z-94294442'}
READ_ONLY = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
CLIENT_REF = {'type': 'string', 'minLength': 8, 'maxLength': 128,
              'description': 'Required stable operation ID (8-128 letters, digits, - or _). Generate once, preserve across retries; new content needs a new ID.'}
TOOLS = [
    {'name': 'relay_overview', 'title': 'Orchestrator overview',
     'description': 'Lead switch plus current and recent orchestration runs (id, title, lead, generation, status, last activity).',
     'inputSchema': {'type': 'object', 'properties': {
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 15},
         'include_archived': {'type': 'boolean', 'default': False}}, 'additionalProperties': False},
     'annotations': READ_ONLY},
    {'name': 'relay_run_status', 'title': 'Run status',
     'description': "One run's lead, checkpoint (completed, next steps, decisions), linked worker tasks, brief excerpt and deliverables.",
     'inputSchema': {'type': 'object', 'properties': {'run_id': RUN_ID}, 'required': ['run_id'],
                     'additionalProperties': False},
     'annotations': READ_ONLY},
    {'name': 'relay_handoff', 'title': 'Handoff snapshot',
     'description': 'Refresh and return the Markdown handoff (runtime/relay/RELAY_HANDOFF.md): active work and the latest lead messages.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
     'annotations': {**READ_ONLY, 'readOnlyHint': False}},
    {'name': 'relay_read_messages', 'title': 'Read messages from the lead',
     'description': ('Messages to Relay from the lead or from Jacob by voice ("from": "user"). By default returns '
                     'only unread ones without consuming them. After receipt, acknowledge their IDs with relay_ack_messages.'),
     'inputSchema': {'type': 'object', 'properties': {
         'unread_only': {'type': 'boolean', 'default': True},
         'mark_read': {'type': 'boolean', 'default': False, 'description': 'Legacy opt-in; marks before transport receipt. Prefer relay_ack_messages.'},
         'run_id': RUN_ID}, 'additionalProperties': False},
     'annotations': {**READ_ONLY, 'readOnlyHint': False, 'idempotentHint': False}},
    {'name': 'relay_ack_messages', 'title': 'Acknowledge received messages',
     'description': 'Mark these exact messages to Relay as read after receiving them. Retrying the same IDs is safe; unknown or other-recipient IDs fail without changes.',
     'inputSchema': {'type': 'object', 'properties': {
         'message_ids': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 200}},
         'required': ['message_ids'], 'additionalProperties': False},
     'annotations': {**READ_ONLY, 'readOnlyHint': False}},
    {'name': 'relay_thread', 'title': 'Conversation log',
     'description': 'Both directions of the Relay/lead mailbox, newest last, without changing read state.',
     'inputSchema': {'type': 'object', 'properties': {
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 40}, 'run_id': RUN_ID},
         'additionalProperties': False},
     'annotations': READ_ONLY},
    {'name': 'relay_post_to_lead', 'title': 'Message the lead',
     'description': ('Append a message from Relay to the lead orchestrator. Use kind "request" to ask for work, '
                     '"handoff" to pass findings or context, "question" for decisions, "reply" to answer a lead message.'),
     'inputSchema': {'type': 'object', 'properties': {
         'subject': {'type': 'string', 'maxLength': relay_bridge.MAX_SUBJECT},
         'body': {'type': 'string', 'maxLength': relay_bridge.MAX_BODY},
         'client_ref': CLIENT_REF,
         'kind': {'type': 'string', 'enum': list(relay_bridge.KINDS), 'default': 'note'},
         'run_id': RUN_ID,
         'reply_to': {'type': 'string', 'description': 'id of the lead message being answered'}},
         'required': ['subject', 'body', 'client_ref'], 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}},
    {'name': 'relay_reply_to_user', 'title': 'Answer Jacob (voice)',
     'description': ('Answer a message Jacob sent by voice through OpenWhispr (from "user"). OpenWhispr reads the answer '
                     'aloud once. kind "question" asks him something and keeps his request open; "reply" or "note" '
                     'is the final answer. reply_to must be the id of his message (or of his spoken answer).'),
     'inputSchema': {'type': 'object', 'properties': {
         'subject': {'type': 'string', 'maxLength': relay_bridge.MAX_SUBJECT},
         'body': {'type': 'string', 'maxLength': relay_bridge.MAX_BODY},
         'client_ref': CLIENT_REF,
         'reply_to': {'type': 'string', 'description': 'id of the user message being answered'},
         'kind': {'type': 'string', 'enum': ['reply', 'question', 'note'], 'default': 'reply'}},
         'required': ['subject', 'body', 'reply_to', 'client_ref'], 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}},
]


MAX_LINE = 1024 * 1024
TYPES = {'string': str, 'boolean': bool, 'integer': int, 'array': list}


class ToolError(ValueError):
    pass


def validated(tool, args):
    """Check arguments against the advertised schema; bool is not accepted as an integer."""
    if not isinstance(args, dict):
        raise ToolError('arguments must be an object')
    schema = tool['inputSchema']
    unexpected = set(args) - set(schema['properties'])
    if unexpected:
        raise ToolError('Unexpected arguments: ' + ', '.join(sorted(unexpected)))
    missing = [key for key in schema.get('required', []) if key not in args]
    if missing:
        raise ToolError('Missing arguments: ' + ', '.join(missing))
    for key, value in args.items():
        spec = schema['properties'][key]
        expected = TYPES[spec['type']]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ToolError(f'{key} must be a {spec["type"]}')
        if 'enum' in spec and value not in spec['enum']:
            raise ToolError(f'{key} must be one of ' + ', '.join(spec['enum']))
        if expected is int and not spec.get('minimum', value) <= value <= spec.get('maximum', value):
            raise ToolError(f'{key} must be between {spec["minimum"]} and {spec["maximum"]}')
        if expected is str and not spec.get('minLength', 0) <= len(value) <= spec.get('maxLength', len(value)):
            raise ToolError(f'{key} has an invalid length')
        if expected is list and (not spec.get('minItems', 0) <= len(value) <= spec.get('maxItems', len(value)) or
                                 any(not isinstance(item, str) for item in value)):
            raise ToolError(f'{key} must contain the permitted number of string IDs')
    return args


def call_tool(name, arguments, workspaces):
    known = next((tool for tool in TOOLS if tool['name'] == name), None)
    if known is None:
        raise ToolError(f'Unknown tool {name}')
    args = validated(known, {} if arguments is None else arguments)
    if name == 'relay_overview':
        return relay_bridge.overview(workspaces, limit=args.get('limit', 15),
                                     include_archived=args.get('include_archived', False))
    if name == 'relay_run_status':
        return relay_bridge.run_status(args['run_id'], workspaces)
    if name == 'relay_handoff':
        written = relay_bridge.write_handoff(workspaces)
        return {**written, 'markdown': Path(written['path']).read_text(encoding='utf-8')}
    if name == 'relay_read_messages':
        return relay_bridge.inbox('relay', unread_only=args.get('unread_only', True),
                                  mark_read=args.get('mark_read', False), run_id=args.get('run_id'))
    if name == 'relay_ack_messages':
        return relay_bridge.acknowledge('relay', args['message_ids'])
    if name == 'relay_thread':
        return relay_bridge.thread(args.get('limit', 40), args.get('run_id'))
    if name == 'relay_reply_to_user':
        return relay_bridge.post('relay', args['subject'], args['body'], to='user',
                                 kind=args.get('kind', 'reply'), reply_to=args['reply_to'], client_ref=args['client_ref'])
    return relay_bridge.post('relay', args['subject'], args['body'], kind=args.get('kind', 'note'),
                             run_id=args.get('run_id'), reply_to=args.get('reply_to'), client_ref=args['client_ref'])


def tool_result(value, error=False):
    text = value.get('markdown') if isinstance(value, dict) and 'markdown' in value else json.dumps(value, indent=2, ensure_ascii=False)
    result = {'content': [{'type': 'text', 'text': text}], 'isError': error}
    if isinstance(value, dict) and not error:
        result['structuredContent'] = value
    return result


def handle(message, workspaces):
    """One JSON-RPC message in, one response (or None for notifications) out."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': message.get('id') if isinstance(message, dict) else None,
                'error': {'code': -32600, 'message': 'Invalid request'}}
    method, ident, params = message['method'], message.get('id'), message.get('params') or {}
    if 'id' not in message:
        return None
    try:
        if method == 'initialize':
            requested = params.get('protocolVersion')
            result = {'protocolVersion': requested if requested in PROTOCOLS else PROTOCOLS[0],
                      'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': SERVER,
                      'instructions': INSTRUCTIONS}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            try:
                result = tool_result(call_tool(params.get('name'), params.get('arguments'), workspaces))
            except (ValueError, OSError, TimeoutError) as error:
                result = tool_result({'error': str(error)}, error=True)
        else:
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': f'Method not found: {method}'}}
    except Exception as error:  # keep the server alive for the next request
        traceback.print_exc(file=sys.stderr)
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32603, 'message': str(error)}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def serve(stdin, stdout, workspaces):
    """Newline-delimited JSON-RPC; batches are answered for clients on pre-2025-06-18 protocols."""
    while raw := stdin.readline(MAX_LINE + 1):
        if len(raw) > MAX_LINE and not raw.endswith(b'\n'):
            while raw and not raw.endswith(b'\n'):
                raw = stdin.readline(MAX_LINE + 1)
            replies = [{'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Request exceeds 1 MiB'}}]
        elif not (line := raw.decode('utf-8', errors='replace').strip()):
            continue
        else:
            try:
                incoming = json.loads(line)
            except ValueError:
                replies = [{'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}]
            else:
                is_batch = isinstance(incoming, list) and bool(incoming)  # [] is one invalid request
                replies = [r for r in (handle(m, workspaces) for m in (incoming if is_batch else [incoming]))
                           if r is not None]
                if is_batch and replies:
                    replies = [replies]
        for reply in replies:
            stdout.write((json.dumps(reply, ensure_ascii=False) + '\n').encode('utf-8'))
            stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--workspace', action='append', default=[], help='Extra project root with a .orchestration folder')
    args = parser.parse_args(argv)
    workspaces = relay_bridge.default_workspaces() + [Path(p) for p in args.workspace]
    serve(sys.stdin.buffer, sys.stdout.buffer, workspaces)
    return 0


if __name__ == '__main__':
    sys.exit(main())
