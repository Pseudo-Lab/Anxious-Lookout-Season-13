"""Review-owned #107 DB/API gate: agent post-blocks saves, loss refusal, pre-#107 replay.

Not run by back (unit tests only); see config_tests/test_agent_post_blocks.py for pure boundaries.
"""
from copy import deepcopy
import json
from uuid import uuid4

from sqlalchemy import text

from app.post_blocks import LEGACY_FORMAT_SUMMARY
from tests.test_research import identity, headers
from tests.test_research_pats import pat_client, issue, call
from tests.test_agent_research_saves import turn
from tests.test_page_chat import callback
from tests.test_posts import make_draft, commit, proposal_content


def node():
    return str(uuid4())


def formatted_post(client, auth):
    """A human-saved post whose second paragraph is colored (Markdown cannot keep it)."""
    content = {'format': 'post-blocks', 'version': 1, 'blocks': [
        {'id': node(), 'type': 'paragraph', 'runs': [{'text': 'Plain', 'marks': []}]},
        {'id': node(), 'type': 'paragraph', 'runs': [{'text': 'Red', 'marks': [{'color': '#FF0000'}]}]}]}
    return commit(client, auth, make_draft(client, auth, content=content))


def save_args(post, content_input):
    value = proposal_content()
    value.update(contentInput=content_input, postId=post['id'], expectedVersion=post['version'], mutationId=str(uuid4()))
    return value


def test_pre107_cached_post_result_replays_same_version_with_succeeded_receipt(client, admin):
    owner, auth = identity(client, admin)
    session, runner = turn(client, owner, auth)
    args = {**proposal_content(content='Plain body'), 'mutationId': str(uuid4())}
    saved = callback(client, session, runner, 'research_post_save', args)
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result['formatChanges'] is None and result['latestVersion']['formatSummary']['lossy'] is False
    # Stand in for a result stored before #107: fixed summary, no formatChanges.
    legacy = deepcopy(result)
    legacy.pop('formatChanges')
    legacy['latestVersion']['formatSummary'] = deepcopy(LEGACY_FORMAT_SUMMARY)
    with admin.begin() as db:
        db.execute(text('UPDATE research.idempotency SET response=CAST(:response AS jsonb) WHERE owner_id=:owner AND key=:key'),
                   {'response': json.dumps(legacy), 'owner': owner, 'key': args['mutationId']})
    replay = callback(client, session, runner, 'research_post_save', args)
    assert replay.status_code == 200, replay.text
    assert replay.json() == legacy
    versions = client.get('/api/research/posts/' + result['id'] + '/versions').json()['items']
    assert len(versions) == 1
    receipts = client.get('/api/research/sessions/' + session['id'] + '/research-calls').json()['items']
    assert [item['status'] for item in receipts] == ['succeeded', 'succeeded']
    assert receipts[0]['saved']['versionId'] == receipts[1]['saved']['versionId'] == result['latestVersion']['id']


def test_pre107_cached_post_result_replays_on_the_pat_path(pat_client, admin):
    owner, auth = identity(pat_client, admin)
    token = issue(pat_client, auth)['token']
    args = {**proposal_content(content='Plain body'), 'mutationId': str(uuid4())}
    saved = call(pat_client, token, 'research_post_save', args)
    assert saved.status_code == 200, saved.text
    legacy = deepcopy(saved.json())
    legacy.pop('formatChanges')
    legacy['latestVersion']['formatSummary'] = deepcopy(LEGACY_FORMAT_SUMMARY)
    with admin.begin() as db:
        db.execute(text('UPDATE research.idempotency SET response=CAST(:response AS jsonb) WHERE owner_id=:owner AND key=:key'),
                   {'response': json.dumps(legacy), 'owner': owner, 'key': args['mutationId']})
    replay = call(pat_client, token, 'research_post_save', args)
    assert replay.status_code == 200, replay.text
    assert replay.json() == legacy
    with admin.connect() as db:
        statuses = db.execute(text("SELECT status FROM research.pat_audit WHERE owner_id=:owner AND operation='research_post_save'"),
                              {'owner': owner}).scalars().all()
    assert sorted(statuses) == ['succeeded', 'succeeded']


def test_markdown_save_on_formatted_post_is_refused_then_allowed(client, admin):
    owner, auth = identity(client, admin)
    post = formatted_post(client, auth)
    assert post['latestVersion']['formatSummary']['omittedProperties'] == ['color']
    session, runner = turn(client, owner, auth)
    refused = callback(client, session, runner, 'research_post_save', save_args(post, {'format': 'markdown', 'text': 'Rewritten'}))
    assert refused.status_code == 422, refused.text
    error = refused.json()['error']
    assert error['code'] == 'formatting_would_be_lost'
    assert error['details']['properties'] == ['color'] and 'post-block-patch' in error['details']['hint']
    assert client.get('/api/research/posts/' + post['id']).json()['version'] == post['version']
    allowed = callback(client, session, runner, 'research_post_save',
                       save_args(post, {'format': 'markdown', 'text': 'Rewritten', 'allowFormatLoss': True}))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['latestVersion']['formatSummary']['lossy'] is False


def test_patch_save_keeps_untouched_formatting_ids_and_cas(client, admin):
    owner, auth = identity(client, admin)
    post = formatted_post(client, auth)
    plain, red = post['latestVersion']['content']['blocks']
    session, runner = turn(client, owner, auth)
    args = save_args(post, {'format': 'post-block-patch', 'operations': [
        {'op': 'replace', 'id': plain['id'], 'block': {**plain, 'runs': [{'text': 'Plain, revised', 'marks': []}]}}]})
    saved = callback(client, session, runner, 'research_post_save', args)
    assert saved.status_code == 200, saved.text
    result = saved.json()
    blocks = result['latestVersion']['content']['blocks']
    assert [block['id'] for block in blocks] == [plain['id'], red['id']] and blocks[1] == red
    assert [change['baseBlockId'] for change in result['formatChanges']] == [plain['id']]
    assert callback(client, session, runner, 'research_post_save', args).json() == result
    stale = callback(client, session, runner, 'research_post_save', {**args, 'mutationId': str(uuid4())})
    assert stale.status_code == 409
    bad = save_args({**post, 'version': result['version']}, {'format': 'post-block-patch', 'operations': [
        {'op': 'insert', 'afterId': red['id'], 'block': {'id': node(), 'type': 'paragraph', 'runs': [{'text': 'x', 'marks': ['bogus']}]}}]})
    rejected = callback(client, session, runner, 'research_post_save', bad)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()['error']['details']['path'].startswith('/contentInput/operations/0/block/runs/0/marks')
    human = client.get('/api/research/posts/' + post['id']).json()
    assert human['version'] == result['version'] and human['latestVersion']['content'] == result['latestVersion']['content']


def unhashable_format_args():
    # review 7e44e7a blocker: an object/array format returned 500 on the PAT and owner Codex paths.
    for bad in ({}, ['markdown']):
        args = {**proposal_content(), 'mutationId': str(uuid4())}
        args['contentInput'] = {'format': bad, 'text': 'x'}
        yield args


def assert_format_pointer(reply):
    assert reply.status_code == 422, reply.text
    error = reply.json()['error']
    assert error['code'] == 'validation_error' and error['details']['path'] == '/contentInput/format'


def test_unhashable_format_is_a_422_pointer_on_the_codex_path(client, admin):
    owner, auth = identity(client, admin)
    session, runner = turn(client, owner, auth)
    for args in unhashable_format_args():
        assert_format_pointer(callback(client, session, runner, 'research_post_save', args))


def test_unhashable_format_is_a_422_pointer_on_the_pat_path(pat_client, admin):
    owner, auth = identity(pat_client, admin)
    token = issue(pat_client, auth)['token']
    for args in unhashable_format_args():
        assert_format_pointer(call(pat_client, token, 'research_post_save', args))
