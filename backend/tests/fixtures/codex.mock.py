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
trace = path.parent / "fixture-model-rpc.json"
observed = json.loads(trace.read_text()) if trace.exists() else []


def emit(body):
    print(json.dumps(body), flush=True)


def save():
    path.write_text(json.dumps(records))


for line in sys.stdin:
    body = json.loads(line)
    method, identity, params = body.get("method"), body.get("id"), body.get("params", {})
    if method in {"thread/start", "thread/resume", "turn/start"}:
        observed.append({"method": method, "model": params.get("model")})
        trace.write_text(json.dumps(observed))
    if method in {"initialize", "account/login/start"}:
        emit({"id": identity, "result": {}})
    elif method == "initialized":
        pass
    elif method == "account/read":
        emit({"id": identity, "result": {"account": {"type": "chatgpt", "email": "fixture-provider-identity@example.invalid"}}})
    elif method == "thread/start":
        thread = str(uuid.uuid4())
        records[thread] = {"id": thread, "turns": [], "tools": params["dynamicTools"]}
        save()
        emit({"id": identity, "result": {"thread": records[thread], "model": params["model"]}})
    elif method == "thread/resume":
        emit({"id": identity, "result": {"thread": records[params["threadId"]], "model": records[params["threadId"]].get("returnModel", params["model"])}})
    elif method == "thread/read":
        emit({"id": identity, "result": {"thread": records[params["threadId"]]}})
    elif method == "turn/start":
        prompt = params["input"][0]["text"]
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
        if prompt == "/fixture/rerouted":
            emit({"method": "model/rerouted", "params": {"threadId": thread, "turnId": turn, "fromModel": params["model"], "toModel": "fixture-wrong-model", "reason": "highRiskCyberActivity"}})
        tool = "research_page_context" if "/fixture/page-context" in prompt or "/fixture/page-reference " in prompt else "research_document_save"
        args = {} if tool == "research_page_context" else {"title": "Fixture document", "content": "Complete fixture tool input", "mutationId": str(uuid.uuid4())}
        if prompt == "/fixture/credential-tool":
            args["content"] = json.loads((path.parent / "auth.json").read_text())["tokens"]["access_token"]
        if "/fixture/page-reference " in prompt:
            args = {"materialId": prompt.split("/fixture/page-reference ", 1)[1].strip()}
        tool_id = "tool-" + turn
        pending = {"thread": thread, "turn": turn, "items": [{"id": "user-" + turn, "type": "userMessage", "content": params["input"]},
                   {"id": tool_id, "type": "dynamicToolCall", "tool": tool, "arguments": args}]}
        emit({"id": tool_id, "method": "item/tool/call", "params": {"threadId": thread, "turnId": turn,
                    "callId": tool_id, "tool": tool, "arguments": args}})
        emit({"id": identity, "result": {"turn": {"id": turn, "model": "fixture-wrong-model" if prompt == "/fixture/model-response" else params["model"]}}})
    elif method == "turn/interrupt":
        emit({"id": identity, "result": {}})
    elif pending and identity == "tool-" + pending["turn"]:
        result = body.get("result", {"success": False, "contentItems": []})
        pending["items"][-1].update(contentItems=result["contentItems"], status="completed" if result["success"] else "failed")
        pending["items"].append({"id": "answer-" + pending["turn"], "type": "agentMessage", "text": "Offline fixture answer"})
        records[pending["thread"]]["turns"].append({"id": pending["turn"], "items": pending["items"]})
        save()
        emit({"method": "turn/completed", "params": {"threadId": pending["thread"], "turn": {"id": pending["turn"], "status": "completed"}}})
        pending = None
