"""Real API/DB scope admission and callbacks; synthetic runner, no provider."""
import json
import uuid

import pytest
from sqlalchemy import text

from app.migrate import migrate
from app.conversations import display_native_item
from tests.test_conversations import create_session, FixtureRunner
from tests.test_research import create, identity, headers, material
from tests.test_policy_publication import connect, publish


@pytest.fixture(scope="module", autouse=True)
def page_schema(schema):
    migrate("0004_publication")


class HeldRunner:
    health = FixtureRunner.health

    def __init__(self, client, owner):
        self.dispatches, self.last, self.record, self.state = 0, None, {"turns": []}, "running"
        client.app.state.codex_verification = "fixture"
        client.app.state.research_runners[owner] = self

    def submit(self, identity, body):
        self.dispatches += 1
        self.last = body

    def read(self, identity):
        return {"requestId": self.last["requestId"], "state": self.state, "turnId": "held-turn" if self.record["turns"] else None,
                "threadId": "same-native-thread", "record": self.record}


def callback(client, session, runner, name, arguments, **overrides):
    return client.post("/api/internal/research/tools", json={"sessionId": session["id"], "requestId": runner.last["requestId"],
                       "name": name, "arguments": arguments, **overrides}, headers={"Authorization": "Bearer " + runner.last["toolToken"]})


@pytest.mark.parametrize("state", ["running", "failed", "legacy"])
def test_nullable_tool_history_remains_readable_with_no_fabricated_result(client, admin, state):
    owner, auth = identity(client, admin)
    session = create_session(client, auth)
    root = "/api/research/sessions/" + session["id"]
    tool = {"id": "nullable-tool", "type": "dynamicToolCall", "tool": "research_get", "arguments": {},
            "status": "inProgress" if state == "running" else "failed", "contentItems": None,
            "success": None, "durationMs": None}
    record = {"turns": [{"id": "held-turn", "items": [
        {"id": "user", "type": "userMessage", "content": [{"type": "text", "text": "Original old question"}]}, tool,
        {"id": "answer", "type": "agentMessage", "text": "Preserved answer"}]}]}
    if state == "legacy":
        # Before page scopes existed, a failed native result could already be null.
        with admin.begin() as db:
            db.execute(text("UPDATE research.conversations SET state='failed',native_record=CAST(:record AS jsonb) WHERE id=:id"),
                       {"id": session["id"], "record": json.dumps(record)})
    else:
        runner = HeldRunner(client, owner)
        assert client.post(root + "/messages", json={"text": "Current question", "expectedVersion": 1}, headers=headers(auth)).status_code == 202
        runner.state, runner.record = state, record
    detail = client.get(root)
    assert detail.status_code == 200, detail.text
    items = detail.json()["items"]
    projected = next(item for item in items if item["type"] == "tool_call")
    assert projected["status"] == tool["status"] and projected["output"] is None and "error" not in projected
    assert items[0]["text"] == ("Original old question" if state == "legacy" else "Current question")
    assert any(item.get("text") == "Preserved answer" for item in items)
    raw = client.get(root + "/items/nullable-tool")
    assert raw.status_code == 200 and raw.json()["raw"] == tool
    with admin.connect() as db:
        saved = db.execute(text("SELECT native_record FROM research.conversations WHERE id=:id"), {"id": session["id"]}).scalar_one()
    assert saved["turns"][0]["items"][1] == tool


def test_rejected_result_and_later_nullable_result_keep_separate_statuses():
    rejected = {"id": "denied", "type": "dynamicToolCall", "tool": "research_archive", "status": "failed",
                "contentItems": [{"type": "inputText", "text": json.dumps({"error": {"code": "tool_not_allowed"}})}]}
    nullable = {"id": "pending", "type": "dynamicToolCall", "tool": "research_get", "status": "inProgress", "contentItems": None}
    assert display_native_item(rejected)[0]["status"] == "rejected"
    assert display_native_item(rejected)[0]["error"] == {"code": "tool_not_allowed"}
    assert display_native_item(nullable)[0]["status"] == "inProgress"
    assert display_native_item(nullable)[0]["output"] is None


@pytest.mark.parametrize("fields,status,expected", [
    ({}, "inProgress", "inProgress"),
    ({"contentItems": None}, "failed", "failed"),
    ({"contentItems": []}, "failed", "failed"),
    ({"contentItems": [{"type": "inputText", "text": '{"content":"saved"}'}]}, "completed", "completed"),
    ({"contentItems": [{"type": "inputText", "text": '{"error":{"code":"tool_not_allowed"}}'}]}, "failed", "rejected"),
])
def test_native_tool_result_shapes_preserve_output_and_only_label_scope_rejection(fields, status, expected):
    item = {"id": "call", "type": "dynamicToolCall", "tool": "research_get", "status": status, **fields}
    projected = display_native_item(item)[0]
    assert projected["status"] == expected and projected["output"] == fields.get("contentItems")
    assert projected.get("error") == ({"code": "tool_not_allowed"} if expected == "rejected" else None)


@pytest.mark.parametrize("page", [{"kind": "unknown"}, {"kind": "material"}, {"kind": "document", "resourceId": "bad"},
    {"kind": "general", "resourceId": str(uuid.uuid4())}, {"kind": "general", "resourceId": None},
    {"kind": "general", "allowedTools": ["research_archive"]}, {"kind": "general", "title": "client title"}])
def test_invalid_descriptor_is_rejected_without_admission(client, admin, page):
    owner, auth = identity(client, admin)
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    reply = client.post("/api/research/sessions/" + session["id"] + "/messages", json={"text": "hello", "expectedVersion": 1, "page": page}, headers=headers(auth))
    assert reply.status_code == 422 and runner.dispatches == 0
    assert client.get("/api/research/sessions/" + session["id"]).json()["version"] == 1


def test_general_descriptor_grants_owner_tools_not_old_public_context(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, "documents")
    publish(client, auth, doc)
    session = client.post("/api/research/sessions", json={"title": "Old context", "publicDocumentId": doc["id"]}, headers=headers(auth)).json()
    runner = HeldRunner(client, owner)
    reply = client.post("/api/research/sessions/" + session["id"] + "/messages", json={"text": "normal question", "expectedVersion": 1}, headers=headers(auth))
    assert reply.status_code == 202
    from app.page_scope import OWNER_TOOLS
    assert reply.json()["page"] == {"kind": "general"} and reply.json()["allowedTools"] == OWNER_TOOLS and reply.json()["capability"] == "owner_research"
    assert runner.last["scope"] == {"page": {"kind": "general"}, "context": None}
    assert len(runner.last["tools"]) == 13  # Stable catalog is not effective permission.
    assert callback(client, session, runner, "research_list", {"type": "document"}).status_code == 200
    assert callback(client, session, runner, "research_document_save", {
        "mutationId": str(uuid.uuid4()), "title": "Draft", "content": "Draft"}).status_code == 200
    rejected = callback(client, session, runner, "research_page_context", {})
    assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "tool_not_allowed"
    assert len(client.get("/api/research/documents").json()["items"]) == 2


def test_foreign_archived_wrong_type_and_missing_pages_fail_before_runner_and_key_reservation(client, admin):
    _, first_auth = identity(client, admin)
    foreign = create(client, first_auth, "documents")
    owner, auth = identity(client, admin, "222222")
    archived = create(client, auth, "documents")
    client.request("DELETE", "/api/research/documents/" + archived["id"], json={"expectedVersion": 1}, headers=headers(auth))
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    root = "/api/research/sessions/" + session["id"]
    key = str(uuid.uuid4())
    for kind, resource in [("document", foreign["id"]), ("document", archived["id"]), ("material", foreign["id"]), ("public_document", foreign["id"]), ("document", str(uuid.uuid4()))]:
        reply = client.post(root + "/messages", json={"text": "question", "expectedVersion": 1, "page": {"kind": kind, "resourceId": resource}}, headers=headers(auth, key))
        assert reply.status_code == 404 and runner.dispatches == 0
    # Rejected attempts do not consume/reserve the key.
    assert client.post(root + "/messages", json={"text": "question", "expectedVersion": 1}, headers=headers(auth, key)).status_code == 202


@pytest.mark.parametrize("kind", ["material", "document"])
def test_detail_scope_fixed_on_acceptance_reads_updates_conflicts_and_history(client, admin, kind):
    owner, auth = identity(client, admin)
    target = create(client, auth, kind + "s")
    other = create(client, auth, kind + "s", title="Another item")
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    root = "/api/research/sessions/" + session["id"]
    page = {"kind": kind, "resourceId": target["id"]}
    body = {"text": "work on this", "expectedVersion": 1, "page": page}
    key = str(uuid.uuid4())
    accepted = client.post(root + "/messages", json=body, headers=headers(auth, key))
    assert accepted.status_code == 202 and accepted.json()["capability"] == "owner_research"
    assert runner.last["scope"]["context"]["version"] == 1
    changed = {**body, "page": {"kind": kind, "resourceId": other["id"]}}
    assert client.post(root + "/messages", json=changed, headers=headers(auth, key)).json()["error"]["code"] == "idempotency_conflict"
    assert client.post(root + "/messages", json={**changed, "expectedVersion": 2}, headers=headers(auth)).status_code == 409
    assert client.post(root + "/messages", json=body, headers=headers(auth, key)).json() == accepted.json()
    assert runner.dispatches == 1
    for name in ("research_get", "research_versions", "research_relations"):
        assert callback(client, session, runner, name, {"type": kind, "id": target["id"]}).status_code == 200
        assert callback(client, session, runner, name, {"type": kind, "id": other["id"]}).status_code == 200
        assert callback(client, session, runner, name, {"type": "relation", "id": target["id"]}).status_code == (404 if name == "research_get" else 422)
    version_args = {"type": kind, "id": target["id"], "versionId": other["latestVersion"]["id"]}
    assert callback(client, session, runner, "research_version_get", version_args).status_code == 404
    version_args["versionId"] = target["latestVersion"]["id"]
    assert callback(client, session, runner, "research_version_get", version_args).json()["content"] == target["content"]
    for name in ("research_archive", "research_relation_save", "research_relation_update", "research_list", "research_document_save", "research_material_save"):
        assert callback(client, session, runner, name, {}).status_code == 422
    edited = material(title="UI edit") if kind == "material" else {"title": "UI edit", "content": "Concurrent UI body"}
    assert client.patch("/api/research/" + kind + "s/" + target["id"], json={**edited, "expectedVersion": 1}, headers=headers(auth)).status_code == 200
    update = {**edited, "id": target["id"], "expectedVersion": 1, "mutationId": str(uuid.uuid4())}
    assert callback(client, session, runner, "research_" + kind + "_update", update).status_code == 409
    update.update(expectedVersion=2, title="Tool edit", mutationId=str(uuid.uuid4()))
    assert callback(client, session, runner, "research_" + kind + "_update", update).json()["version"] == 3
    with admin.connect() as db:
        stored = db.execute(text("SELECT native_record FROM research.conversations WHERE id=:id"), {"id": session["id"]}).scalar_one()
        assert stored["platformRequests"][0]["scope"]["context"]["version"] == 1
    # Native payload cannot replace authority; history displays the accepted question/page.
    runner.record = {"platformRequests": [{"requestId": runner.last["requestId"], "scope": {"page": {"kind": "material_workspace"}}}],
                     "turns": [{"id": "held-turn", "items": [{"id": "user", "type": "userMessage", "content": [{"type": "text", "text": "injected preamble"}]}]}]}
    runner.state = "idle"
    detail = client.get(root).json()
    assert detail["items"][0]["text"] == "work on this" and detail["items"][0]["page"] == accepted.json()["page"]
    assert detail["page"] == accepted.json()["page"]
    assert callback(client, session, runner, "research_get", {"type": kind, "id": target["id"]}).status_code == 404
    # Same conversation, changing the page does not remove owner authority.
    runner.record = {"turns": runner.record["turns"]}
    runner.state = "running"
    next_turn = client.post(root + "/messages", json={"text": "normal followup", "expectedVersion": detail["version"], "page": {"kind": "general"}}, headers=headers(auth))
    assert next_turn.status_code == 202 and runner.last["expectedThreadId"] == "same-native-thread"
    assert callback(client, session, runner, "research_get", {"type": kind, "id": target["id"]}).status_code == 200


@pytest.mark.parametrize("kind", ["material", "document"])
def test_workspace_allows_both_owned_types_but_never_foreign_data(client, admin, kind):
    _, other_auth = identity(client, admin)
    foreign = create(client, other_auth, kind + "s")
    owner, auth = identity(client, admin, "222222")
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    accepted = client.post("/api/research/sessions/" + session["id"] + "/messages", json={"text": "collect", "expectedVersion": 1,
        "page": {"kind": kind + "_workspace"}}, headers=headers(auth))
    assert accepted.status_code == 202 and accepted.json()["capability"] == "owner_research"
    data = material() if kind == "material" else {"title": "Tool draft", "content": "Tool body"}
    data["mutationId"] = str(uuid.uuid4())
    created = callback(client, session, runner, "research_" + kind + "_save", data)
    assert created.status_code == 200
    listed = callback(client, session, runner, "research_list", {"type": kind}).json()["items"]
    assert [item["id"] for item in listed] == [created.json()["id"]] and foreign["id"] not in json.dumps(listed)
    for name, args in [("research_list", {"type": "document" if kind == "material" else "material"}),
                       ("research_get", {"type": kind, "id": created.json()["id"]})]:
        assert callback(client, session, runner, name, args).status_code == 200
    assert callback(client, session, runner, "research_get", {"type": kind, "id": foreign["id"]}).status_code == 404
    assert callback(client, session, runner, "research_archive", {}).status_code == 422


def test_public_scope_freezes_release_refs_and_idempotent_reply_after_withdrawal(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, "documents")
    ref = create(client, auth, content="Accepted complete reference")
    unrelated = create(client, auth, content="Private unrelated material")
    connect(client, auth, doc, ref)
    release = publish(client, auth, doc)
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    root = "/api/research/sessions/" + session["id"]
    body = {"text": "read publication", "expectedVersion": 1, "page": {"kind": "public_document", "resourceId": doc["id"]}}
    key = str(uuid.uuid4())
    accepted = client.post(root + "/messages", json=body, headers=headers(auth, key))
    assert accepted.status_code == 202 and accepted.json()["capability"] == "public_context_read"
    assert client.request("DELETE", "/api/research/documents/" + doc["id"] + "/publication", json={"expectedVersion": 2}, headers=headers(auth)).status_code == 200
    assert callback(client, session, runner, "research_page_context", {}).json()["id"] == release["id"]
    assert callback(client, session, runner, "research_page_context", {"materialId": ref["id"]}).json()["content"] == "Accepted complete reference"
    assert callback(client, session, runner, "research_page_context", {"materialId": unrelated["id"]}).status_code == 403
    assert callback(client, session, runner, "research_document_save", {}).status_code == 403
    assert client.post(root + "/messages", json=body, headers=headers(auth, key)).json() == accepted.json() and runner.dispatches == 1
    fresh = create_session(client, auth)
    assert client.post("/api/research/sessions/" + fresh["id"] + "/messages", json=body, headers=headers(auth)).status_code == 404


@pytest.mark.parametrize('page_kind', ['general', 'material', 'document', 'material_workspace', 'document_workspace'])
def test_owner_workflow_on_any_page_preserves_versions_ownership_and_archive(client, admin, page_kind):
    _, foreign_auth = identity(client, admin)
    foreign = create(client, foreign_auth)
    owner, auth = identity(client, admin, '222222')
    page = {'kind': page_kind}
    hint = None
    if page_kind in {'material', 'document'}:
        hint = create(client, auth, page_kind+'s')
        page['resourceId'] = hint['id']
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    accepted = client.post('/api/research/sessions/'+session['id']+'/messages',
        json={'text': 'Collect, revise and draft', 'expectedVersion': 1, 'page': page}, headers=headers(auth))
    assert accepted.status_code == 202
    assert accepted.json()['capability'] == 'owner_research'
    assert len(accepted.json()['allowedTools']) == 12

    def call(name, args, status=200):
        response = callback(client, session, runner, name, args)
        assert response.status_code == status, response.text
        return response.json()

    call('research_get', {'type': 'material', 'id': foreign['id']}, 404)
    collected = {**material(), 'mutationId': str(uuid.uuid4())}
    saved = call('research_material_save', collected)
    assert call('research_material_save', collected) == saved
    edited = call('research_material_update', {**material(content='Revised excerpt'), 'id': saved['id'],
                  'expectedVersion': 1, 'mutationId': str(uuid.uuid4())})
    call('research_material_update', {**material(), 'id': saved['id'],
         'expectedVersion': 1, 'mutationId': str(uuid.uuid4())}, 409)
    assert edited['version'] == 2
    versions = call('research_versions', {'type': 'material', 'id': saved['id']})
    assert len(versions['items']) == 2
    old = call('research_version_get', {'type': 'material', 'id': saved['id'], 'versionId': saved['latestVersion']['id']})
    assert old['content'] == collected['content']
    draft = call('research_document_save', {'title': 'Draft', 'content': 'Cites [source](https://example.com/article).',
                'mutationId': str(uuid.uuid4())})
    relation = call('research_relation_save', {'source': {'type': 'document', 'id': draft['id']},
        'target': {'type': 'material', 'id': saved['id']}, 'kind': 'references', 'directed': True,
        'mutationId': str(uuid.uuid4())})
    call('research_relation_save', {'source': {'type': 'document', 'id': draft['id']},
        'target': {'type': 'material', 'id': foreign['id']}, 'kind': 'references', 'directed': True,
        'mutationId': str(uuid.uuid4())}, 404)
    revised_relation = call('research_relation_update', {'id': relation['id'], 'kind': 'references',
        'description': 'Supports the draft', 'expectedVersion': 1, 'mutationId': str(uuid.uuid4())})
    assert revised_relation['version'] == 2
    assert call('research_get', {'type': 'relation', 'id': relation['id']})['id'] == relation['id']
    assert call('research_relations', {'type': 'document', 'id': draft['id']})['items']
    # The viewed resource is only a hint. Archiving it cannot remove permission
    # to work on a different owned item during this same admitted turn.
    if hint:
        call('research_archive', {'type': page_kind, 'id': hint['id'], 'expectedVersion': 1,
             'mutationId': str(uuid.uuid4())})
    updated = call('research_document_update', {'id': draft['id'], 'title': 'Revised draft',
        'content': draft['content']+' New paragraph.', 'expectedVersion': 1, 'mutationId': str(uuid.uuid4())})
    assert '[source](https://example.com/article)' in updated['content']
    call('research_archive', {'type': 'relation', 'id': relation['id'], 'expectedVersion': 2,
         'mutationId': str(uuid.uuid4())})
    archived = call('research_archive', {'type': 'material', 'id': saved['id'], 'expectedVersion': 2,
         'mutationId': str(uuid.uuid4())})
    assert archived['archived'] is True
    assert len(call('research_versions', {'type': 'material', 'id': saved['id']})['items']) == 2
    assert call('research_get', {'type': 'material', 'id': foreign['id']}, 404)
