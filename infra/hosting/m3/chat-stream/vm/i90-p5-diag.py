"""#90 P5 S0 diagnosis (PM / review 2026-11-15), development VM only, READ ONLY: why the S0 record shows no tool item.
No runner entry, session, turn, replay or deployment change; nothing is written on the guest. Run as root on the guest
with the reviewed bytes on stdin (the sheet checks their sha256 on the development server first):

    sudo -n python3 -I -B - < i90-p5-diag.py

It keeps four facts apart (review): (1) native persistence - the conversation's native record: turn statuses and item
type counts, nativeThreadId present; (2) invocation - the thread's Codex rollout (native PV codex/sessions): tool-call
records by kind, the code-mode `exec` calls and the nested tool names their input names (allowlisted names only:
research_* and exec_command, anything else counted as `other`), their outputs; (3) callback logging coverage - whether
the api runs with `--no-access-log` (then a tool callback leaves no api log line and its absence proves nothing);
(4) runner logs - the S0 runner Pod is gone; only the filtered evidence lines remain.

Output: JSON lines (kind + fields), counts / statuses / allowlisted names only - never prompts, answer text, tool
arguments or results, raw native JSON, ids (the conversation and thread only as an 8-char sha256 prefix) or
credentials. The last line is `diagnosis`: `a` (the rollout is intact and has no tool-call record: the model did not
call a tool), `b` (the rollout has a research tool call - a code-mode exec naming tools.research_* or a function call
named research_* - while the native record has no tool item: invoked, not persisted as an item), or `unknown` (the
rollout is missing, unreadable, ambiguous, or the record and the rollout disagree otherwise). rc: 0 diagnosed (any of
the three); 3 refused (not root, evidence or the record not found).
"""
import hashlib
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
MESSAGE_ITEMS = {'userMessage', 'agentMessage'}
TOOL_ITEMS = {'dynamicToolCall', 'mcpToolCall', 'commandExecution', 'fileChange'}
NAMES = re.compile(r'\btools\.([A-Za-z_]\w*)')
LIMIT = 64 << 20


class Refused(Exception):
    pass


def out(kind, **fields):
    print(json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


def short(value):
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def allow(name):
    return name if isinstance(name, str) and (name.startswith('research_') or name in ('exec', 'exec_command')) \
        else 'other'


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
    rows = [json.loads(line) for line in output.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    return rows


def s0_conversation():
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
    out('s0', evidence=runs[0], conversation=short(conversation), rc=done.get('rc'),
        S0=(done.get('verdicts') or {}).get('S0'), started=value.get('started'), finished=done.get('finished'))
    return conversation


def native_record(conversation):
    """(1) The native record in memory: only counts, statuses and type names leave this function."""
    rows = sql("SELECT json_build_object('r',native_record,'state',state,'updated',updated_at,'version',version) "
               "FROM research.conversations WHERE id='%s' AND owner_id='%s';" % (conversation, OWNER))
    if len(rows) != 1 or not isinstance(rows[0].get('r'), dict):
        raise Refused('the conversation record is not there exactly once')
    record = rows[0]['r']
    turns = record.get('turns') if isinstance(record.get('turns'), list) else []
    shape = []
    for turn in turns:
        items = turn.get('items') if isinstance(turn, dict) and isinstance(turn.get('items'), list) else []
        types = {}
        for item in items:
            key = item.get('type') if isinstance(item, dict) and isinstance(item.get('type'), str) else '<none>'
            key = key if key in MESSAGE_ITEMS | TOOL_ITEMS else 'other'
            types[key] = types.get(key, 0) + 1
        shape.append({'status': turn.get('status') if isinstance(turn, dict) and turn.get('status') in (
            'completed', 'interrupted', 'failed', 'inProgress') else 'other', 'itemTypes': types})
    thread = record.get('nativeThreadId') if isinstance(record.get('nativeThreadId'), str) else None
    out('native', state=rows[0].get('state'), version=rows[0].get('version'), updated=rows[0].get('updated'),
        keys=sorted(k for k in record if isinstance(k, str))[:40], turns=shape,
        platformRequests=len(record.get('platformRequests') or []),
        unrecordedInputs=len(record.get('unrecordedInputs') or []), thread=short(thread) if thread else None)
    tool_items = sum(t['itemTypes'].get(k, 0) for t in shape for k in TOOL_ITEMS)
    return thread, tool_items


def rollout(thread):
    """(2) The thread's rollout: event kinds and tool-call records, names allowlisted, nothing else."""
    if not thread or not re.fullmatch(r'[0-9A-Za-z-]{1,200}', thread):
        out('rollout', found=False, why='no nativeThreadId')
        return None
    try:
        base = os.lstat(CODEX_SESSIONS)
        if not stat.S_ISDIR(base.st_mode):
            raise OSError()
    except OSError:
        out('rollout', found=False, why='codex/sessions absent or not a directory')
        return None
    found = []
    for folder, dirs, files in os.walk(CODEX_SESSIONS, followlinks=False):
        dirs[:] = [d for d in dirs if stat.S_ISDIR(os.lstat(os.path.join(folder, d)).st_mode)]
        found += [os.path.join(folder, f) for f in files
                  if f.startswith('rollout-') and f.endswith('-%s.jsonl' % thread)]
    if len(found) != 1:
        out('rollout', found=False, why='%d rollout files for the thread' % len(found))
        return None
    path = found[0]
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
        out('rollout', found=False, why='not a regular file or too large')
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        lines = stream.read(LIMIT + 1).splitlines()
    kinds, unreadable, calls, outputs, functions, nested, other_calls, tool_events = {}, 0, {}, 0, {}, {}, 0, 0
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        payload = event.get('payload') if isinstance(event, dict) and isinstance(event.get('payload'), dict) else {}
        kind, sub = event.get('type') if isinstance(event, dict) else None, payload.get('type')
        key = '%s/%s' % (kind, sub) if isinstance(sub, str) else str(kind)
        kinds[key] = kinds.get(key, 0) + 1
        if kind == 'response_item' and sub == 'custom_tool_call':
            name = allow(payload.get('name'))
            calls[name] = calls.get(name, 0) + 1
            if isinstance(payload.get('input'), str):
                for each in NAMES.findall(payload['input']):
                    nested[allow(each)] = nested.get(allow(each), 0) + 1
        elif kind == 'response_item' and sub == 'custom_tool_call_output':
            outputs += 1
        elif kind == 'response_item' and sub == 'function_call':
            name = allow(payload.get('name'))
            functions[name] = functions.get(name, 0) + 1
        elif kind == 'response_item' and isinstance(sub, str) and sub.endswith('_call'):
            other_calls += 1
        elif kind == 'event_msg' and isinstance(sub, str) and any(w in sub for w in ('tool', 'mcp', 'exec', 'call')):
            tool_events += 1
    facts = {'found': True, 'file': short(os.path.basename(path)), 'lines': len(lines), 'unreadable': unreadable,
             'eventKinds': dict(sorted(kinds.items())), 'customToolCalls': calls, 'customToolOutputs': outputs,
             'nestedToolNames': nested, 'functionCalls': functions, 'otherCallRecords': other_calls,
             'toolEvents': tool_events}
    out('rollout', **facts)
    return facts


def coverage():
    """(3) Is a tool callback logged by the api at all?"""
    rc, output = run([K3S, 'kubectl', '-n', 'm2-hosting', 'get', 'deployment', 'api', '-o', 'json'])
    try:
        container = [c for c in json.loads(output)['spec']['template']['spec']['containers'] if c['name'] == 'api'][0]
        argv = (container.get('command') or []) + (container.get('args') or [])
        quiet = '--no-access-log' in argv
    except Exception:
        quiet = None
    out('callbackLogging', apiNoAccessLog=quiet,
        meaning='a tool callback leaves no api log line; its absence proves nothing' if quiet is not False
        else 'access lines exist; not read by this diagnosis (bounded scope)')


def runner_logs(evidence_name):
    """(4) The S0 runner Pod is gone; only its filtered evidence lines remain."""
    path = os.path.join(EVIDENCE, evidence_name, 'runner-s0.log')
    try:
        count = len(open(path, 'rb').read().splitlines())
    except Exception:
        count = None
    out('runnerLogs', podLogs='unavailable (the gen 35 Pod was deleted by the s0 stop; not restarted)',
        filteredEvidenceLines=count)


def diagnose(tool_items, facts):
    if facts is None or facts.get('unreadable'):
        return 'unknown', 'the rollout is missing or has unreadable lines'
    research = sum(v for k, v in facts['nestedToolNames'].items() if k.startswith('research_')) + \
        sum(v for k, v in facts['functionCalls'].items() if k.startswith('research_'))
    any_call = sum(facts['customToolCalls'].values()) + sum(facts['functionCalls'].values()) + \
        facts['otherCallRecords']
    if research and not tool_items:
        return 'b', 'the rollout has %d research tool reference(s) in tool calls; the native record has no tool item' \
            % research
    if not any_call and not facts['customToolOutputs'] and not facts['toolEvents'] and not tool_items:
        return 'a', 'the rollout is intact and has no tool-call record'
    return 'unknown', 'tool records exist but none names a research tool, or the record has tool items'


def main():
    if os.geteuid() != 0:
        out('refused', why='not root')
        return 3
    try:
        conversation = s0_conversation()
        evidence = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-p5-s0-\d{8}T\d{6}Z', x))[0]
        thread, tool_items = native_record(conversation)
        facts = rollout(thread)
        coverage()
        runner_logs(evidence)
    except Refused as error:
        out('refused', why=str(error))
        return 3
    verdict, why = diagnose(tool_items, facts)
    out('diagnosis', result=verdict, why=why)
    return 0


if __name__ == '__main__':
    sys.exit(main())
