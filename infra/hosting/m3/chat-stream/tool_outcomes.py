"""#94 (2) tool outcome look (read only): which research tool calls a Codex rollout recorded in a time window, and what
each call's output said - fixed names and fixed error codes only, never an argument, a result, a message or a path.

Why: production showed `도구 : failed` on a turn whose runner timing had `toolCalls: 1, toolMs: 697`, and the api runs
without an access log, so nothing records what the tool callback returned. The Codex rollout of that turn holds the
tool call (`response_item/function_call`, the dynamic tool name) and its output (`function_call_output`): the runner's
reply text is the api's JSON body or the runner's own `{"error": {"code": ...}}` (runner/app.py `_reply_tool`).

Input: the owner's Codex sessions directory (mounted read only), and a UTC window. Every `rollout-*.jsonl` file below it
(at most 3 levels: YYYY/MM/DD) is read through lstat + O_NOFOLLOW | O_NONBLOCK + fstat (regular files only, 64 MiB cap);
only lines whose `timestamp` falls in the window count. Nothing is written.

Output: one JSON line:
- `files`: rollout files scanned / with lines in the window (counts only; no name);
- `eventKinds`: counts per fixed kind (+ `other`);
- `calls`: for each research tool `function_call` in the window (name from the fixed TOOLS list, else `other`), paired
  with its `function_call_output` by call id: `output` = `error:<code>` (code from the fixed ERRORS list, else
  `error:other`), `ok-json` (a JSON object without an `error` key), `text` (not JSON), `missing` (no output in the
  window), and `success` when the output payload carries one (true / false / absent);
- `exec`: code-mode `exec` / `exec_command` custom tool calls and outputs in the window, and per fixed error code the
  number of exec outputs whose text contains it (a substring count: a hint, not a call outcome).
Exit codes: 0 measured, 2 refused input.
Usage: python -I tool_outcomes.py /sessions --since 2026-11-15T11:50:00Z --until 2026-11-15T12:10:00Z
"""
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone

LIMIT = 64 << 20
TOOLS = ('research_archive', 'research_document_save', 'research_document_update', 'research_get', 'research_list',
         'research_material_save', 'research_material_update', 'research_page_context', 'research_post_draft_get',
         'research_post_propose', 'research_relation_save', 'research_relation_update', 'research_relations',
         'research_version_get', 'research_versions')  # backend/app/research_tools.py TOOLS
EXEC = ('exec', 'exec_command')  # the code-mode custom tools
ERRORS = ('native_turn_mismatch', 'tool_not_allowed', 'not_ready', 'not_found', 'forbidden', 'unauthenticated',
          'validation_error', 'conflict', 'payload_too_large', 'storage_quota_exceeded', 'idempotency_conflict',
          'content_limit_exceeded', 'post_contract_upgrade_required', 'policy_refused', 'auth_revoked',
          'auth_expired', 'provider_rate_limited', 'model_unavailable', 'unavailable')
KINDS = ('session_meta', 'turn_context', 'compacted', 'event_msg/task_started', 'event_msg/task_complete',
         'event_msg/turn_started', 'event_msg/turn_complete', 'event_msg/turn_aborted', 'event_msg/token_count',
         'event_msg/agent_message', 'event_msg/user_message', 'event_msg/agent_reasoning', 'event_msg/error',
         'event_msg/exec_command_begin', 'event_msg/exec_command_end', 'event_msg/mcp_tool_call_begin',
         'event_msg/mcp_tool_call_end', 'response_item/message', 'response_item/reasoning',
         'response_item/custom_tool_call', 'response_item/custom_tool_call_output', 'response_item/function_call',
         'response_item/function_call_output')
ROLLOUT = re.compile(r'rollout-[A-Za-z0-9T:._-]{1,200}\.jsonl')


def refuse(reason):
    print(json.dumps({'ok': False, 'reason': reason}, sort_keys=True))
    sys.exit(2)


def utc(text):
    if not isinstance(text, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,9})?Z', text):
        return None
    head, _, fraction = text[:-1].partition('.')
    try:
        stamp = datetime.strptime(head, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return stamp.timestamp() + (float('0.' + fraction) if fraction else 0.0)


def read(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
        raise OSError()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_ino != info.st_ino:
            raise OSError()
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                return b''.join(chunks)
            size += len(chunk)
            if size > LIMIT:
                raise OSError()
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def rollouts(root):
    """Paths of rollout-*.jsonl files at most 3 directory levels below root, never following a link."""
    found, stack = [], [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            path = os.path.join(directory, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode) and depth < 3:
                stack.append((path, depth + 1))
            elif stat.S_ISREG(info.st_mode) and ROLLOUT.fullmatch(name):
                found.append(path)
    return sorted(found)


def texts(output):
    """The text(s) of a function_call_output payload `output`: a string, or content items with `text`."""
    if isinstance(output, str):
        return [output]
    if isinstance(output, dict) and isinstance(output.get('content'), (str, list)):
        return texts(output['content'])
    if isinstance(output, list):
        return [item['text'] for item in output if isinstance(item, dict) and isinstance(item.get('text'), str)]
    return []


def classify(output):
    found = texts(output)
    if not found:
        return 'text'
    for text in found:
        try:
            value = json.loads(text)
        except ValueError:
            continue
        if isinstance(value, dict):
            error = value.get('error')
            if isinstance(error, dict):
                code = error.get('code')
                return 'error:' + (code if code in ERRORS else 'other')
            if 'error' not in value:
                return 'ok-json'
    return 'text'


def success_of(payload):
    output = payload.get('output')
    for holder in (payload, output if isinstance(output, dict) else {}):
        if isinstance(holder.get('success'), bool):
            return holder['success']
    return 'absent'


def measure(root, since, until):
    kinds, calls, outputs, scanned, used = {}, {}, {}, 0, 0
    order = []
    execs = {'calls': 0, 'outputs': 0, 'errorTextHints': {}}
    exec_ids = set()
    for path in rollouts(root):
        try:
            data = read(path)
        except OSError:
            continue
        scanned += 1
        hit = False
        for raw in data.splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(line, dict):
                continue
            stamp = utc(line.get('timestamp'))
            if stamp is None or not since <= stamp <= until:
                continue
            hit = True
            kind, payload = line.get('type'), line.get('payload')
            payload = payload if isinstance(payload, dict) else {}
            sub = payload.get('type')
            name = kind if kind in ('session_meta', 'turn_context', 'compacted') else '%s/%s' % (kind, sub)
            name = name if name in KINDS else 'other'
            kinds[name] = kinds.get(name, 0) + 1
            call = payload.get('call_id')
            call = call if isinstance(call, str) and 0 < len(call) <= 200 else None
            if name == 'response_item/function_call' and call:
                tool = payload.get('name')
                if tool in EXEC:
                    exec_ids.add(call)
                    execs['calls'] += 1
                else:
                    calls[call] = tool if tool in TOOLS else 'other'
                    order.append(call)
            elif name == 'response_item/custom_tool_call' and call:
                if payload.get('name') in EXEC:
                    exec_ids.add(call)
                    execs['calls'] += 1
            elif name in ('response_item/function_call_output', 'response_item/custom_tool_call_output') and call:
                if call in exec_ids:
                    execs['outputs'] += 1
                    joined = '\n'.join(texts(payload.get('output')))
                    for code in ERRORS:
                        if code in joined:
                            execs['errorTextHints'][code] = execs['errorTextHints'].get(code, 0) + 1
                else:
                    outputs[call] = (classify(payload.get('output')), success_of(payload))
        used += hit
    listed = []
    for call in order:
        output, success = outputs.get(call, ('missing', 'absent'))
        listed.append({'tool': calls[call], 'output': output, 'success': success})
    return {'ok': True, 'files': {'scanned': scanned, 'inWindow': used}, 'eventKinds': kinds,
            'calls': listed, 'unpairedOutputs': sum(1 for call in outputs if call not in calls),
            'exec': execs}


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) != 5 or args[1] != '--since' or args[3] != '--until':
        refuse('usage')
    since, until = utc(args[2]), utc(args[4])
    if since is None or until is None or not since < until or until - since > 6 * 3600:
        refuse('window')
    try:
        info = os.lstat(args[0])
    except OSError:
        refuse('directory_missing')
    if not stat.S_ISDIR(info.st_mode):
        refuse('directory_not_a_directory')
    print(json.dumps(measure(args[0], since, until), sort_keys=True))
