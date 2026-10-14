"""Offline link/health readers plus disposable DB; never live data or native RPC."""
import copy
import json
import threading
import uuid
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from sqlalchemy import text

from common_chatbot_links import compare
from common_chatbot_probe import api_links, health_check, health_fields, opaque, runner_links
from common_chatbot_prepare import receipt, patch
from test_common_chatbot_prepare import bundle, step
from test_common_chatbot_preservation import state
from test_common_chatbot_database import research_schema, schema, admin, client, create_session, identity


@pytest.fixture
def links(state, bundle):
    native, control = state
    runner = runner_links(native, control, "synthetic-owner", "synthetic-state")
    now = datetime.now(timezone.utc).isoformat()
    runner["checkedAt"] = now
    session = runner["sessions"][0]
    db = {"admissionHeld": True, "checkedAt": now, "ownerDigest": runner["ownerDigest"], "stateDigest": runner["stateDigest"],
        "sessions": [{"session": session["session"], "request": session["request"], "thread": session["thread"],
        "state": "idle", "error": None, "archived": False, "version": 2, "recordSha256": "a" * 64,
        "turns": session["turns"],
        "platformRequests": [{"request": session["request"], "turn": session["turns"][0]}]}]}
    anchor = {key: runner[key] for key in ("protectedState", "lifetimeReservations")}
    current = copy.deepcopy(bundle[0]["objects"])
    for action in ("hold-api", "stop-runner", "upgrade-runner", "upgrade-api", "upgrade-web", "start-runner"):
        step(bundle, current, action)
    return db, copy.deepcopy(db), runner, anchor, current


def test_links_health_receipt_and_resume_require_actual_reader_proofs(links, bundle):
    before, after, native, anchor, current = links
    link_proof = compare(*links)
    now = datetime.now(timezone.utc).isoformat()
    api = {"checkedAt": now, "admissionHeld": True, "schemaReady": True, "apiRunning": 0, "ambiguous": 0}
    observed = {"checkedAt": now, "apiHoldObserved": True, "referenceMetadataVerified": True,
        "runnerPodCount": 1, "backupPodCount": 0, "apiReady": True, "webReady": True, "runnerReady": True,
        "runnerPodDigest": opaque("pod", "pod"),
        "podImagesVerified": True, "publicReadVerified": True}
    health = {**health_fields({"ownerId": "synthetic-owner", "stateId": "synthetic-state", "podUid": "pod",
        "model": "gpt-6.1-sol", "verification": "unverified", "available": False, "reason": "not_configured"},
        "synthetic-owner", "synthetic-state", "pod"), "checkedAt": now}
    proof = receipt(current, api, native, observed, preservation=anchor, links_check=link_proof, health_check=health)
    assert proof["authAvailable"] is False and proof["dbNativeLinksVerified"] is True
    assert patch(*bundle, current, "resume-api", proof)["target"]["name"] == "api"
    for removed in ("dbNativeLinksVerified", "authCheckVerified"):
        bad = copy.deepcopy(proof); del bad[removed]
        with pytest.raises(ValueError): patch(*bundle, current, "resume-api", bad)
    for modified in ({**health, "checkedAt": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
                     {**health, "authAvailable": True}, {**health, "authCheckVerified": False}, {**health,"ownerDigest":"wrong"},
                     {**health,"stateDigest":"wrong"}, {**health,"podDigest":"f"*64}):
        with pytest.raises(ValueError): receipt(current, api, native, observed, preservation=anchor, links_check=link_proof, health_check=modified)
    with pytest.raises(ValueError): receipt(current, api, native, observed, preservation=anchor,
        links_check={**link_proof, "currentDigest": "wrong"}, health_check=health)
    with pytest.raises(ValueError): receipt(current, api, native, observed, preservation=anchor,
        links_check={**link_proof, "ownerDigest": "wrong"}, health_check=health)


@pytest.mark.parametrize("damage", ["owner", "state", "thread", "request", "orphan-native", "lost-native", "db-change",
    "future", "stale", "lost-reservation", "platform-turn", "platform-request", "lost-native-turn", "duplicate-db"])
def test_links_refuse_changed_or_unmatched_private_records(links, damage):
    before, after, native, anchor, current = links
    if damage in {"owner", "state"}: native[damage+"Digest"] = "wrong"
    elif damage == "thread": native["sessions"][0]["thread"] = "wrong"
    elif damage == "request": native["sessions"][0]["request"] = "wrong"
    elif damage == "orphan-native": native["sessions"][0]["session"] = "wrong"
    elif damage == "lost-native": native["sessions"] = []
    elif damage == "db-change": after["sessions"][0]["recordSha256"] = "b"*64
    elif damage == "future": native["checkedAt"] = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    elif damage == "stale": after["checkedAt"] = (datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
    elif damage == "lost-reservation": native["lifetimeReservations"] = 0
    elif damage == "lost-native-turn": native["sessions"][0]["turns"] = []
    elif damage == "platform-turn":
        before["sessions"][0]["platformRequests"][0]["turn"] = after["sessions"][0]["platformRequests"][0]["turn"] = "wrong"
    elif damage == "platform-request":
        before["sessions"][0]["platformRequests"][0]["request"] = after["sessions"][0]["platformRequests"][0]["request"] = "wrong"
    else:
        before["sessions"].append(copy.deepcopy(before["sessions"][0]))
        after["sessions"].append(copy.deepcopy(after["sessions"][0]))
    with pytest.raises(ValueError): compare(before, after, native, anchor, current)


def test_empty_and_definitely_unsubmitted_db_sessions_are_explicit(links):
    before, after, native, anchor, current = links
    for value in (before, after):
        value["sessions"].append({"session": opaque("session", "empty"), "request": None, "thread": None,
            "state": "idle", "error": None, "platformRequests": [], "turns": []})
        value["sessions"].append({"session": opaque("session", "rejected"), "request": opaque("request", "rejected"),
            "thread": None, "state": "failed", "error": "codex_rejected", "platformRequests": [], "turns": []})
    assert compare(*links)["definiteUnreservedFailures"] == 1
    for value in (before, after): value["sessions"][-1]["error"] = "codex_unavailable"
    with pytest.raises(ValueError): compare(*links)


@pytest.mark.parametrize("change", [{"ownerId":"foreign"}, {"stateId":"foreign"}, {"podUid":"foreign"}, {"model":"other"},
    {"verification":"fixture"}, {"available":True}, {"available":0}, {"reason":"private provider text"}])
def test_health_refuses_wrong_binding_model_fixture_or_untrusted_status(change):
    value = {"ownerId":"owner", "stateId":"state", "podUid":"pod", "model":"gpt-6.1-sol",
             "verification":"unverified", "available":False, "reason":"not_configured"}
    with pytest.raises(ValueError): health_fields({**value, **change}, "owner", "state", "pod")


def test_health_local_authenticated_get_has_no_proxy_redirect_or_secret_output(monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization"), self.headers.get("X-Runner-State-Id")))
            if getattr(self.server, "redirect", False):
                self.send_response(302); self.send_header("Location", "/forbidden"); self.end_headers(); return
            value = {"ownerId":"owner", "stateId":"state", "podUid":"pod", "model":"gpt-6.1-sol",
                     "verification":"unverified", "available":False, "reason":"not_configured", "private":"never-output"}
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())
    server = HTTPServer(("127.0.0.1", 8080), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        for key, value in {"RUNNER_TOKEN_FILE":"/run/transport/transport-token", "RUNNER_ACCOUNT_ID":"owner",
                          "RUNNER_STATE_ID":"state", "RUNNER_POD_UID":"pod", "http_proxy":"http://untrusted.invalid"}.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(Path, "read_text", lambda self: "synthetic-transport-" + "x"*40)
        result = health_check()
        assert requests == [("/health", "Bearer synthetic-transport-"+"x"*40, "state")]
        assert result["authReason"] == "not_configured" and "never-output" not in json.dumps(result)
        assert "synthetic-transport" not in json.dumps(result)
        server.redirect = True
        with pytest.raises(urllib.error.HTTPError): health_check()
        assert len(requests) == 2 and all(value[0] == "/health" for value in requests)
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)


def test_api_link_snapshot_uses_owner_filter_retains_archived_rows_and_never_mutates(client, admin, monkeypatch, tmp_path):
    owner, auth = identity(client, admin)
    first, archived = create_session(client, auth), create_session(client, auth)
    _, foreign_auth = identity(client, admin, number="222222")
    foreign = create_session(client, foreign_auth)
    request = str(uuid.uuid4())
    native = {"nativeThreadId":"native-thread", "turns":[{"id":"native-turn","text":"private-native-sentinel"}],
              "platformRequests":[{"requestId":request,"turnId":"native-turn","text":"private-question-sentinel"}]}
    with admin.begin() as db:
        db.execute(text("UPDATE research.conversations SET request_id=:request,native_record=CAST(:record AS jsonb) WHERE id=:id"),
            {"id":first["id"], "request":request, "record":json.dumps(native)})
        db.execute(text("UPDATE research.conversations SET archived=true WHERE id=:id"), {"id":archived["id"]})
    mapping = tmp_path / "mapping.json"; mapping.write_text(json.dumps({str(owner):{"stateId":"synthetic-state"}}))
    monkeypatch.setenv("CODEX_PERSONAL_ENABLE", "false"); monkeypatch.setenv("CODEX_PERSONAL_ACCOUNT_ID", str(owner))
    monkeypatch.setenv("CODEX_RUNNERS_FILE", str(mapping))
    with admin.connect() as db: original = db.execute(text("SELECT * FROM research.conversations ORDER BY id")).all()
    before, after = api_links(), api_links()
    assert before == after and len(before["sessions"]) == 2
    encoded = json.dumps(before)
    assert all(value not in encoded for value in (str(owner), first["id"], request, "native-thread", "private-native-sentinel", "private-question-sentinel"))
    assert any(row["archived"] is True for row in before["sessions"])
    assert all(row["session"] != opaque("session", foreign["id"]) for row in before["sessions"])
    with admin.connect() as db: assert original == db.execute(text("SELECT * FROM research.conversations ORDER BY id")).all()
