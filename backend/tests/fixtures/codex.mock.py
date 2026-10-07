#!/usr/local/bin/python
"""Offline JSON-lines protocol fixture, not a model or native Codex binary."""
import json
import os
import sys
import uuid
from pathlib import Path

path = Path(os.environ["CODEX_HOME"]) / "fixture-native-record.json"
records = json.loads(path.read_text()) if path.exists() else {}
pending = None


def emit(body):
    print(json.dumps(body), flush=True)


def save():
    path.write_text(json.dumps(records))


for line in sys.stdin:
    body = json.loads(line)
    method, identity, params = body.get("method"), body.get("id"), body.get("params", {})
    if method in {"initialize", "account/login/start"}:
        emit({"id": identity, "result": {}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        thread = str(uuid.uuid4())
        records[thread] = {"id": thread, "turns": [], "tools": params["dynamicTools"]}
        save()
        emit({"id": identity, "result": {"thread": records[thread]}})
    elif method == "thread/resume":
        emit({"id": identity, "result": {"thread": records[params["threadId"]]}})
    elif method == "thread/read":
        emit({"id": identity, "result": {"thread": records[params["threadId"]]}})
    elif method == "turn/start":
        thread, turn = params["threadId"], str(uuid.uuid4())
        args = {"title": "Fixture document", "content": "Complete fixture tool input", "mutationId": str(uuid.uuid4())}
        tool_id = "tool-" + turn
        pending = {"thread": thread, "turn": turn, "items": [{"id": "user-" + turn, "type": "userMessage", "content": params["input"]},
                   {"id": tool_id, "type": "dynamicToolCall", "tool": "research_document_save", "arguments": args}]}
        emit({"id": identity, "result": {"turn": {"id": turn}}})
        emit({"id": tool_id, "method": "item/tool/call", "params": {"threadId": thread, "turnId": turn,
                    "callId": tool_id, "tool": "research_document_save", "arguments": args}})
    elif pending and identity == "tool-" + pending["turn"]:
        result = body["result"]
        pending["items"][-1].update(contentItems=result["contentItems"], status="completed" if result["success"] else "failed")
        pending["items"].append({"id": "answer-" + pending["turn"], "type": "agentMessage", "text": "Offline fixture answer"})
        records[pending["thread"]]["turns"].append({"id": pending["turn"], "items": pending["items"]})
        save()
        emit({"method": "turn/completed", "params": {"threadId": pending["thread"], "turn": {"id": pending["turn"], "status": "completed"}}})
        pending = None
