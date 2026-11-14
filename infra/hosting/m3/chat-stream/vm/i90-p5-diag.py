"""#90 P5 S0 observation r1 (PM 2026-11-15 scope; review diag r0), development VM only, READ ONLY: counts about the
S0 conversation, with no a/b conclusion. No runner entry, session, turn, replay or deployment change; nothing is
written on the guest. Run as root on the guest with the reviewed bytes on stdin (the block checks their sha256 on the
development server first):

    sudo -n python3 -I -B - < i90-p5-diag.py

Output: JSON lines whose keys and values come only from the fixed name lists below; anything else is counted in an
`unknown` bucket, never echoed (review r0 major: no arbitrary string from the record, the rollout or the names):
- `s0`: the s0 result (rc, the S0 verdict) - its evidence directory name is a fixed pattern;
- `native`: item counts per known type, turn counts per known status, known top-level key presence (+ unknown keys
  counted), platformRequests / unrecordedInputs counts, nativeThreadId present (yes / no);
- `rollout`: found (yes / no and a fixed reason), lines, unreadable lines, event counts per known kind (+ unknown),
  for the record's last turn: its start / end event counts and order, the session_meta thread match - coverage facts,
  not a completeness claim;
- `references`: tool names referenced in code-mode exec inputs and named by function calls, per exact known tool name
  (+ other) - **references only, not invocation evidence** (review r0 major: a reference in code is not a call);
  correlated function_call / function_call_output pairs by call id are counted separately;
- `callbackLogging`: whether the api runs with --no-access-log; if it does, callback lines: not logged (count n/a);
- `runnerLogs`: Pod logs unavailable (the S0 Pod was deleted by the stop); filtered evidence line count;
- `conclusion`: the fixed sentence "observed counts only; invocation unknown unless correlated".
rc: 0 reported; 3 refused (not root, the s0 evidence or the record not found exactly once).
"""
import json
import os
import re
import stat
import subprocess
import sys

OWNER = '00000000-0000-4000-8000-000000005805'
STATE_HEX = '75780e81ef7f4aeca59ced9d71f4061a'
EVIDENCE = '/root/issue90/evidence'
CODEX_SESSIONS = '/var/lib/anxious-lookout/user-runners/%s/native/data/codex/sessions' % STATE_HEX
K3S = '/usr/local/bin/k3s'
LIMIT = 64 << 20
ITEM_TYPES = ('userMessage', 'agentMessage', 'reasoning', 'dynamicToolCall', 'mcpToolCall', 'commandExecution',
              'fileChange', 'webSearch')
TURN_STATUSES = ('completed', 'interrupted', 'failed', 'inProgress')
RECORD_KEYS = ('turns', 'nativeThreadId', 'platformRequests', 'unrecordedInputs', 'displayOnly', 'displayOnlyDropped')
EVENT_KINDS = ('session_meta', 'turn_context', 'compacted', 'event_msg/task_started', 'event_msg/task_complete',
               'event_msg/turn_started', 'event_msg/turn_complete', 'event_msg/turn_aborted', 'event_msg/token_count',
               'event_msg/agent_message', 'event_msg/user_message', 'event_msg/agent_reasoning', 'event_msg/error',
               'event_msg/exec_command_begin', 'event_msg/exec_command_end', 'event_msg/mcp_tool_call_begin',
               'event_msg/mcp_tool_call_end', 'response_item/message', 'response_item/reasoning',
               'response_item/custom_tool_call', 'response_item/custom_tool_call_output',
               'response_item/function_call', 'response_item/function_call_output')
TOOL_NAMES = ('research_archive', 'research_document_save', 'research_document_update', 'research_get',
              'research_list', 'research_material_save', 'research_material_update', 'research_page_context',
              'research_post_draft_get', 'research_post_propose', 'research_relation_save',
              'research_relation_update', 'research_relations', 'research_version_get', 'research_versions',
              'exec', 'exec_command')  # backend/app/research_tools.py TOOLS + the code-mode exec tools
START, END = ('task_started', 'turn_started'), ('task_complete', 'turn_complete', 'turn_aborted')
NAMES = re.compile(r'\btools\.([A-Za-z_]\w*)')
CONCLUSION = 'observed counts only; invocation unknown unless correlated'


class Refused(Exception):
    pass


def out(kind, **fields):
    print(json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


def bucket(counts, value, known):
    key = value if value in known else 'unknown'
    counts[key] = counts.get(key, 0) + 1


def run(cmd, stdin=None, timeout=60):
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
                              check=False)
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace')


def sql(query):
    text = 'BEGIN READ ONLY;\n/*i90p5diag*/ %s\nCOMMIT;\n' % query
    rc, output = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U',
                      'postgres', '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], text.encode())
    if rc != 0:
        raise Refused('database lookup failed')
    try:
        return [json.loads(line) for line in output.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Refused('database result unreadable') from None


def s0_evidence():
    runs = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-p5-s0-\d{8}T\d{6}Z', x))
    if len(runs) != 1:
        raise Refused('not exactly one s0 evidence directory')
    try:
        value = json.load(open(os.path.join(EVIDENCE, runs[0], 'run.json')))
        done = json.load(open(os.path.join(EVIDENCE, runs[0], 'exit.json')))
    except Exception:
        raise Refused('the s0 run.json / exit.json is unreadable') from None
    conversation = value.get('conversation')
    if not (isinstance(conversation, str) and re.fullmatch(r'[0-9a-f-]{36}', conversation)):
        raise Refused('the s0 run.json has no conversation')
    verdict = (done.get('verdicts') or {}).get('S0') if isinstance(done.get('verdicts'), dict) else None
    out('s0', evidence=runs[0], rc=done.get('rc') if type(done.get('rc')) is int else None,
        S0=verdict if verdict in (True, False, None) else None)
    return runs[0], conversation


def native(conversation):
    """The record in memory; only fixed-list counts leave. Returns (thread, last turn id) for correlation only."""
    rows = sql("SELECT json_build_object('r',native_record) FROM research.conversations WHERE id='%s' AND "
               "owner_id='%s';" % (conversation, OWNER))
    if len(rows) != 1 or not isinstance(rows[0].get('r'), dict):
        raise Refused('the conversation record is not there exactly once')
    record = rows[0]['r']
    turns = record.get('turns') if isinstance(record.get('turns'), list) else []
    items, statuses = {}, {}
    for turn in turns:
        turn = turn if isinstance(turn, dict) else {}
        bucket(statuses, turn.get('status'), TURN_STATUSES)
        for item in turn.get('items') if isinstance(turn.get('items'), list) else []:
            bucket(items, item.get('type') if isinstance(item, dict) else None, ITEM_TYPES)
    keys = {k: k in record for k in RECORD_KEYS}
    thread = record.get('nativeThreadId') if isinstance(record.get('nativeThreadId'), str) else None
    last = turns[-1].get('id') if turns and isinstance(turns[-1], dict) and isinstance(turns[-1].get('id'), str) \
        else None
    out('native', turns=len(turns), turnStatuses=statuses, itemTypes=items, keys=keys,
        unknownKeys=sum(1 for k in record if k not in RECORD_KEYS),
        platformRequests=len(record['platformRequests']) if isinstance(record.get('platformRequests'), list) else None,
        unrecordedInputs=len(record['unrecordedInputs']) if isinstance(record.get('unrecordedInputs'), list) else None,
        threadPresent=thread is not None, lastTurnIdPresent=last is not None)
    return thread, last


def rollout_lines(thread):
    """(lines, None) or (None, a fixed reason)."""
    if not thread or not re.fullmatch(r'[0-9A-Za-z-]{1,200}', thread):
        return None, 'no thread id'
    try:
        if not stat.S_ISDIR(os.lstat(CODEX_SESSIONS).st_mode):
            raise OSError()
    except OSError:
        return None, 'sessions directory absent'
    found = []
    for folder, dirs, files in os.walk(CODEX_SESSIONS, followlinks=False):
        dirs[:] = [d for d in dirs if stat.S_ISDIR(os.lstat(os.path.join(folder, d)).st_mode)]
        found += [os.path.join(folder, f) for f in files
                  if f.startswith('rollout-') and f.endswith('-%s.jsonl' % thread)]
    if len(found) != 1:
        return None, 'no single rollout file for the thread'
    info = os.lstat(found[0])
    if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
        return None, 'not a regular file or too large'
    fd = os.open(found[0], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(LIMIT + 1).splitlines(), None


def rollout(thread, turn):
    lines, why = rollout_lines(thread)
    if lines is None:
        out('rollout', found=False, reason=why)
        out('references', execReferences={}, functionCalls={}, correlatedFunctionPairs=0, available=False)
        return
    kinds, unreadable, starts, ends, meta_match, aborted = {}, 0, [], [], None, False
    references, functions, calls, answers = {}, {}, {}, {}
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        payload = event.get('payload') if isinstance(event, dict) and isinstance(event.get('payload'), dict) else {}
        kind, sub = event.get('type') if isinstance(event, dict) else None, payload.get('type')
        bucket(kinds, '%s/%s' % (kind, sub) if isinstance(sub, str) else kind, EVENT_KINDS)
        if kind == 'session_meta':
            meta_match = payload.get('id') == thread
        if kind == 'event_msg' and sub in START and payload.get('turn_id') == turn:
            starts.append(index)
        if kind == 'event_msg' and sub in END and payload.get('turn_id') == turn:
            ends.append(index)
            aborted = aborted or sub == 'turn_aborted'
        if kind == 'response_item' and sub == 'custom_tool_call' and isinstance(payload.get('input'), str):
            for name in NAMES.findall(payload['input']):
                bucket(references, name, TOOL_NAMES)
        if kind == 'response_item' and sub == 'function_call':
            bucket(functions, payload.get('name'), TOOL_NAMES)
            calls.setdefault(payload.get('call_id'), []).append(index)
        if kind == 'response_item' and sub == 'function_call_output':
            answers.setdefault(payload.get('call_id'), []).append(index)
    pairs = sum(1 for k, v in calls.items() if isinstance(k, str) and k and len(v) == 1
                and len(answers.get(k, [])) == 1 and answers[k][0] > v[0])
    ordered = len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0]
    out('rollout', found=True, lines=len(lines), unreadable=unreadable, eventKinds=kinds,
        lastTurn={'starts': len(starts), 'ends': len(ends), 'startBeforeEnd': ordered, 'aborted': aborted},
        sessionMetaThreadMatch=meta_match)
    out('references', execReferences=references, functionCalls=functions, correlatedFunctionPairs=pairs,
        available=True, note='references only, not invocation evidence')


def coverage():
    rc, output = run([K3S, 'kubectl', '-n', 'm2-hosting', 'get', 'deployment', 'api', '-o', 'json'])
    try:
        container = [c for c in json.loads(output)['spec']['template']['spec']['containers'] if c['name'] == 'api'][0]
        quiet = '--no-access-log' in (container.get('command') or []) + (container.get('args') or [])
    except Exception:
        quiet = None
    # review scope ack: unavailable / not collected is never reported as an observed zero
    out('callbackLogging', apiNoAccessLog=quiet, callbackLines='unavailable (api runs --no-access-log)' if quiet else
        ('not collected (access log on; outside this bounded scope)' if quiet is False else 'unavailable (unknown)'),
        observedZero=False)


def runner_logs(evidence):
    try:
        count = len(open(os.path.join(EVIDENCE, evidence, 'runner-s0.log'), 'rb').read().splitlines())
    except Exception:
        count = None
    out('runnerLogs', podLogs='unavailable', filteredEvidenceLines=count)


def main():
    if os.geteuid() != 0:
        out('refused', reason='not root')
        return 3
    try:
        evidence, conversation = s0_evidence()
        thread, turn = native(conversation)
        rollout(thread, turn)
        coverage()
        runner_logs(evidence)
    except Refused as error:
        out('refused', reason=str(error))
        return 3
    out('conclusion', text=CONCLUSION)
    return 0


if __name__ == '__main__':
    sys.exit(main())
