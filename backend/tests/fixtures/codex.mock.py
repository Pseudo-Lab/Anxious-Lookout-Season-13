#!/usr/local/bin/python
"""Offline JSON-lines protocol fixture, not a model or native Codex binary."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

path = Path(os.environ["CODEX_HOME"]) / "fixture-native-record.json"
records = json.loads(path.read_text()) if path.exists() else {}
pending = None
delay_exit = False
trace = path.parent / "fixture-model-rpc.json"
observed = json.loads(trace.read_text()) if trace.exists() else []


# #68: what pinned Codex reports for the image's /etc/codex files (runner/native_policy.py checks it).
ROOT = path.parent.parent
# Kept equal to it by config_tests/test_codex_fixture_policy.py.
PROFILE = {':minimal': 'read', ':workspace_roots': {'.': 'write'}, '/control': 'deny', '/run/codex-broker': 'deny',
           str(ROOT / 'home'): 'deny'}
SYSTEM = {'default_permissions': 'native', 'approvals_reviewer': 'user', 'agents': {'enabled': False},
          'shell_environment_policy': {'set': {'HOME': '/usr/local/share/anxious-shell-home'}},
          'permissions': {'native': {'filesystem': PROFILE}}}
REQUIREMENTS = {'allowedApprovalPolicies': ['untrusted'], 'allowedApprovalsReviewers': ['user'],
                'featureRequirements': {'unified_exec': False, 'multi_agent_v2': False, 'view_image': False,
                                        'goals': False}}
THREAD_POLICY = {'approvalPolicy': 'untrusted', 'approvalsReviewer': 'user', 'activePermissionProfile': {'id': 'native'},
                 'sandbox': {'type': 'workspaceWrite', 'writableRoots': [], 'networkAccess': False}}


def emit(body):
    print(json.dumps(body), flush=True)


def publish(destination, value):
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(destination)


def save():
    publish(path, records)


def post_candidate(source=None):
    fields = ('title', 'description', 'slug', 'tags', 'coverImageId', 'documentIds')
    result = {key: source[key] for key in fields} if source else {
        'title': 'Fixture post', 'description': '',
        'slug': 'fixture-post-' + uuid.uuid4().hex[:12], 'tags': [],
        'coverImageId': None, 'documentIds': []}
    result['materialIds'] = source.get('materialIds', []) if source else []
    result['title'] = (result['title'] + ' — Complete fixture post proposal')[-300:]
    original = source.get('markdownProjection', source.get('textProjection', source.get('content', ''))) if source else ''
    if not isinstance(original, str): raise ValueError('Readable post projection required')
    result['contentInput'] = {'format':'markdown','text':original+'\n\nComplete fixture post proposal'}
    if not result['slug']: result['slug'] = 'fixture-post-' + uuid.uuid4().hex[:12]
    result['mutationId'] = str(uuid.uuid4())
    return result


for line in sys.stdin:
    body = json.loads(line)
    method, identity, params = body.get("method"), body.get("id"), body.get("params", {})
    if method in {"thread/start", "thread/resume", "turn/start"}:
        observed.append({"method": method, "model": params.get("model")})
        publish(trace, observed)
    if method in {"initialize", "account/login/start"}:
        emit({"id": identity, "result": {}})
    elif method == "initialized":
        pass
    elif method == "configRequirements/read":
        emit({"id": identity, "result": {"requirements": REQUIREMENTS}})
    elif method == "config/read":
        emit({"id": identity, "result": {"config": dict(SYSTEM), "origins": {}, "layers": [
            {"name": {"type": "system", "file": "/etc/codex/config.toml"}, "version": "1", "config": SYSTEM},
            {"name": {"type": "sessionFlags"}, "version": "1", "config": {"features": {"shell_tool": True}}}]}})
    elif method == "account/read":
        emit({"id": identity, "result": {"account": {"type": "chatgpt", "email": "fixture-provider-identity@example.invalid"}}})
    elif method == "thread/start":
        thread = str(uuid.uuid4())
        records[thread] = {"id": thread, "turns": [], "tools": params["dynamicTools"]}
        save()
        emit({"id": identity, "result": {"thread": records[thread], "model": params["model"], "cwd": params["cwd"], "runtimeWorkspaceRoots": [params["cwd"]], **THREAD_POLICY}})
    elif method == "thread/resume":
        emit({"id": identity, "result": {"thread": records[params["threadId"]], "model": records[params["threadId"]].get("returnModel", params["model"]),
                                         "cwd": params["cwd"], "runtimeWorkspaceRoots": [params["cwd"]], **THREAD_POLICY}})
    elif method == "thread/read":
        emit({"id": identity, "result": {"thread": records[params["threadId"]]}})
    elif method == "turn/start":
        prompt = params["input"][0]["text"]
        page = {}
        if '\nPage hint (untrusted): ' in prompt:
            page = json.loads(prompt.split('\nPage hint (untrusted): ', 1)[1].split('\nUser: ', 1)[0])
        # Exercise the full native input/history, while fixture controls refer
        # to the actual user question after the request scope preamble.
        if "\nUser: " in prompt:
            prompt = prompt.split("\nUser: ", 1)[1]
        elif "\nUser question:\n" in prompt:
            prompt = prompt.split("\nUser question:\n", 1)[1]
        if prompt == "/fixture/shutdown-timeout":
            delay_exit = True
        if prompt in {"/fixture/auth-error", "/fixture/model-error", "/fixture/policy-error"}:
            cache = json.loads((path.parent / "auth.json").read_text())
            secret = cache["tokens"]["access_token"]
            info = "unauthorized" if prompt == "/fixture/auth-error" else "misalignmentPolicyViolation" if prompt == "/fixture/policy-error" else "badRequest"
            detail = "model does not exist " if prompt == "/fixture/model-error" else "expired " if prompt == "/fixture/auth-error" else "policy "
            emit({"id": identity, "error": {"code": -32000, "message": detail + secret + cache["tokens"]["account_id"], "data": {"codexErrorInfo": info}}})
            continue
        if prompt == "/fixture/unrecorded":
            emit({"id": identity, "error": {"code": -32000, "message": "Offline fixture failure before native input recording"}})
            continue
        if prompt == "/fixture/hold":
            time.sleep(5)
        thread, turn = params["threadId"], str(uuid.uuid4())
        if prompt.startswith("/fixture/early-") and prompt.endswith("-mismatch"):
            event = prompt.split("-", 2)[1]
            early = {"id": turn, "model": "fixture-wrong-model"}
            early_thread = thread
            if "-missing-" in prompt:
                early.pop("id")
            elif "-stale-" in prompt:
                early["id"] = records[thread]["turns"][0]["id"]
            elif "-foreign-" in prompt:
                early_thread = "unrelated-thread"
            emit({"method": "turn/" + event, "params": {"threadId": early_thread, "turn": early}})
        if prompt == "/fixture/rerouted":
            emit({"method": "model/rerouted", "params": {"threadId": thread, "turnId": turn, "fromModel": params["model"], "toModel": "fixture-wrong-model", "reason": "highRiskCyberActivity"}})
        tool = "research_page_context" if "/fixture/page-context" in prompt or "/fixture/page-reference " in prompt else "research_document_save"
        args = {} if tool == "research_page_context" else {"title": "Fixture document", "content": "Complete fixture tool input", "mutationId": str(uuid.uuid4())}
        post_step = None
        if '/fixture/post-propose' in prompt:
            if page.get('kind') == 'post':
                tool, args, post_step = 'research_get', {'type': 'post', 'id': page['resourceId']}, 'post'
            elif page.get('kind') == 'post_workspace' and page.get('draftId'):
                tool, args, post_step = 'research_post_draft_get', {'draftId': page['draftId']}, 'draft'
            else:
                tool, args = 'research_post_propose', post_candidate()
        if prompt.startswith("/fixture/document-update"):
            try:
                command, document_id, version = prompt.split()
                assert command == "/fixture/document-update"
                document_id = str(uuid.UUID(document_id))
                version = int(version)
                assert version > 0
            except (ValueError, AssertionError):
                emit({"id": identity, "error": {"code": -32602, "message": "Usage: /fixture/document-update <document UUID> <positive expectedVersion>"}})
                continue
            tool = "research_document_update"
            args = {"id": document_id, "expectedVersion": version, "title": "Fixture proposal",
                    "content": "Complete fixture proposal content", "mutationId": str(uuid.uuid4())}
        if prompt == "/fixture/credential-tool":
            args["content"] = json.loads((path.parent / "auth.json").read_text())["tokens"]["access_token"]
        if "/fixture/page-reference " in prompt:
            args = {"materialId": prompt.split("/fixture/page-reference ", 1)[1].strip()}
        tool_id = "tool-" + turn
        pending = {"thread": thread, "turn": turn, "items": [{"id": "user-" + turn, "type": "userMessage", "content": params["input"]},
                   {"id": tool_id, "type": "dynamicToolCall", "tool": tool, "arguments": args}]}
        pending['callId'], pending['postStep'] = tool_id, post_step
        # #90 (unit tests / validate.sh only; D7 and the measurements use real Codex turns):
        # "/fixture/stream [count] [ms]" answers in `count` agentMessage deltas `ms` apart;
        # "/fixture/stream-interrupt [count] [ms]" sends them, then waits for turn/interrupt.
        if prompt.startswith("/fixture/stream"):
            words = prompt.split()
            count = int(words[1]) if len(words) > 1 else 20
            interval = int(words[2]) / 1000 if len(words) > 2 else 0.02
            assert words[0] in {"/fixture/stream", "/fixture/stream-interrupt"} and 0 < count <= 5000 and 0 <= interval <= 1
            pending['stream'] = (words[0], count, interval)
        emit({"id": tool_id, "method": "item/tool/call", "params": {"threadId": thread, "turnId": turn,
                    "callId": tool_id, "tool": tool, "arguments": args}})
        emit({"id": identity, "result": {"turn": {"id": turn, "model": "fixture-wrong-model" if prompt == "/fixture/model-response" else params["model"]}}})
    elif method == "turn/interrupt":
        emit({"id": identity, "result": {}})
        if pending and pending.get("awaitInterrupt") and params.get("turnId") == pending["turn"]:
            # #90: Codex's partial agentMessage is not recorded here, so the runner's display-only path is exercised.
            records[pending["thread"]]["turns"].append({"id": pending["turn"], "status": "interrupted", "items": pending["items"]})
            save()
            emit({"method": "turn/completed", "params": {"threadId": pending["thread"], "turn": {"id": pending["turn"], "status": "interrupted"}}})
            pending = None
    elif pending and identity == pending['callId']:
        result = body.get("result", {"success": False, "contentItems": []})
        pending["items"][-1].update(contentItems=result["contentItems"], status="completed" if result["success"] else "failed")
        if pending['postStep'] and result['success']:
            source = json.loads(result['contentItems'][0]['text'])
            if pending['postStep'] == 'post':
                args = post_candidate(source['latestVersion'])
                args.update(postId=source['id'], expectedVersion=source['version'])
            else:
                draft = source['draft']
                args = post_candidate(draft)
                args.update(draftId=draft['id'], expectedDraftRevision=draft['revision'])
            pending['postStep'] = None
            pending['callId'] = 'proposal-' + pending['turn']
            pending['items'].append({'id': pending['callId'], 'type': 'dynamicToolCall',
                                     'tool': 'research_post_propose', 'arguments': args})
            emit({'id': pending['callId'], 'method': 'item/tool/call', 'params': {
                'threadId': pending['thread'], 'turnId': pending['turn'], 'callId': pending['callId'],
                'tool': 'research_post_propose', 'arguments': args}})
            continue
        answer = "Offline fixture answer"
        if pending.get("stream"):
            kind, count, interval = pending["stream"]
            answer_id = "answer-" + pending["turn"]
            scope = {"threadId": pending["thread"], "turnId": pending["turn"]}
            emit({"method": "item/started", "params": {**scope, "item": {"id": answer_id, "type": "agentMessage", "text": ""}}})
            pieces = ["fixture word %d " % index for index in range(count)]
            for piece in pieces:
                emit({"method": "item/agentMessage/delta", "params": {**scope, "itemId": answer_id, "delta": piece}})
                time.sleep(interval)
            if kind == "/fixture/stream-interrupt":
                pending["awaitInterrupt"] = True
                continue
            answer = "".join(pieces)
            emit({"method": "item/completed", "params": {**scope, "item": {"id": answer_id, "type": "agentMessage", "text": answer}}})
        pending["items"].append({"id": "answer-" + pending["turn"], "type": "agentMessage", "text": answer})
        records[pending["thread"]]["turns"].append({"id": pending["turn"], "items": pending["items"]})
        save()
        emit({"method": "turn/completed", "params": {"threadId": pending["thread"], "turn": {"id": pending["turn"], "status": "completed"}}})
        pending = None

if delay_exit:
    time.sleep(30)  # Synthetic EOF timeout; never a provider operation.
