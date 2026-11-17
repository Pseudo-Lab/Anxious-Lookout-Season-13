"""Success evidence from real serializers; malformed bodies never prove a save."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.research_results import success, failure
from config_tests.research_result_fixtures import save_result, relation_result


def cases():
    documents = {'title': 'Document', 'content': 'Cited source', 'mutationId': str(uuid4())}
    material = {**documents, 'sourceUrl': 'https://example.invalid/article',
                'collectedAt': '2026-11-16T01:00:00+09:00', 'contentKind': 'excerpt'}
    for kind, args in [('document', documents), ('material', material)]:
        yield 'research_' + kind + '_save', args, save_result(kind, args)
        changed = {**args, 'id': str(uuid4()), 'expectedVersion': 3}
        yield 'research_' + kind + '_update', changed, save_result(kind, changed)
    post = {'title': 'Post', 'description': 'Citations', 'tags': [], 'coverImageId': None,
            'documentIds': [], 'materialIds': [], 'contentInput': {'format': 'markdown', 'text': '# Note'},
            'mutationId': str(uuid4())}
    yield 'research_post_save', post, save_result('post', post)
    revised = {**post, 'postId': str(uuid4()), 'expectedVersion': 4}
    yield 'research_post_save', revised, save_result('post', revised)
    relation = {'source': {'type': 'document', 'id': str(uuid4())},
        'target': {'type': 'material', 'id': str(uuid4())}, 'kind': ' cites ',
        'description': 'source', 'directed': True, 'mutationId': str(uuid4())}
    yield 'research_relation_save', relation, relation_result(relation)
    edited = {'id': str(uuid4()), 'expectedVersion': 2, 'kind': 'supports',
              'description': 'updated', 'mutationId': str(uuid4())}
    yield 'research_relation_update', edited, relation_result(edited, update=True)
    archive = {'type': 'document', 'id': str(uuid4()), 'expectedVersion': 5, 'mutationId': str(uuid4())}
    yield 'research_archive', archive, {'id': archive['id'], 'version': 6, 'archived': True}


@pytest.mark.parametrize('name,args,result', list(cases()))
def test_real_serializer_results_pass_response_and_stored_row_validation(name, args, result):
    assert success(name, result, args)
    assert success(name, result)  # SQL success-row structural validation uses the same contract.


@pytest.mark.parametrize('path,value', [
    (('id',), 'not-a-uuid'), (('version',), True), (('archived',), True),
    (('latestVersion', 'id'), str(uuid4())), (('latestVersion', 'number'), True),
    (('latestVersion', 'author'), {'kind':'codex','conversationId':str(uuid4())}),
    (('saved', 'id'), str(uuid4())), (('saved', 'resourceType'), 'post'),
    (('saved', 'versionId'), str(uuid4())), (('saved', 'versionNumber'), True),
    (('saved', 'destination', 'path'), 'https://other.invalid/'),
    (('saved', 'destination', 'resourceId'), str(uuid4())),
    (('saved', 'title'), 'different'), (('createdAt',), 'today'),
    (('content',), {'unexpected': 'object'}),
])
def test_malformed_internal_relationships_fail_both_response_paths(path, value):
    result = save_result()
    target = result
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    assert not success('research_document_save', result)


def test_structurally_valid_other_target_or_cas_is_not_this_callback_evidence():
    args = {'id': str(uuid4()), 'title': 'document', 'content': '# saved',
            'expectedVersion': 3, 'mutationId': str(uuid4())}
    result = save_result(arguments=args)
    assert success('research_document_update', result, args)
    assert not success('research_document_update', result, {**args, 'id': str(uuid4())})
    assert not success('research_document_update', result, {**args, 'expectedVersion': 1})
    assert not success('research_document_update', result, {**args, 'content': 'another body'})


def test_serialized_result_keeps_version_identity_without_exposing_conversation_author():
    original = str(uuid4())
    result = save_result(conversation=original)
    assert success('research_document_save', result)
    assert 'author' not in result['latestVersion'] and original not in str(result)


def test_relation_endpoint_and_archive_contracts_cannot_accept_health_dicts():
    for name, args, result in cases():
        assert not success(name, {'ok': True}, args)
        if name.startswith('research_relation'):
            malformed = deepcopy(result)
            malformed['target']['id'] = malformed['source']['id']
            assert not success(name, malformed)
        if name == 'research_archive':
            assert not success(name, {**result, 'archived': False}, args)


def test_failure_requires_the_complete_error_contract():
    assert failure({'error': {'code': 'conflict', 'message': 'Reload first'}}, {'conflict'})
    assert not failure({'error': {'code': 'conflict'}}, {'conflict'})
    assert not failure({'error': {'code': 'other', 'message': 'Unknown'}}, {'conflict'})
