"""Validate API mutation results before producing a successful research receipt.

Shared by API dispatch and idempotent responses. This is structural evidence
from trusted serializers, not proof of commit or permission
derived from a model-supplied saved link. No DB or network operations occur here.
"""
from datetime import datetime
from uuid import UUID


def require(condition):
    if not condition:
        raise ValueError('invalid_mutation_result')


def identity(value):
    require(isinstance(value, str) and str(UUID(value)) == value)
    return value


def positive(value):
    require(type(value) is int and value > 0)
    return value


def timestamp(value):
    require(isinstance(value, str))
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None)
    return result


def text(value, maximum, *, nonempty=False):
    require(isinstance(value, str) and len(value) <= maximum and '\x00' not in value)
    value.encode('utf-8')
    require(not nonempty or bool(value.strip()))
    return value


def common(value):
    require(isinstance(value, dict) and 'error' not in value)
    identity(value['id'])
    positive(value['version'])


def provenance(version):
    author = version['author']
    require(isinstance(author, dict))
    if author.get('kind') == 'pat':
        require(set(author) == {'kind', 'conversationId', 'credentialId', 'name'} and author['conversationId'] is None)
        identity(author['credentialId'])
        text(author['name'], 80, nonempty=True)
    else:
        require(set(author) == {'kind', 'conversationId'} and author['kind'] == 'codex')
        identity(author['conversationId'])
    # An identical idempotent replay may belong to an earlier conversation.
    # Preserve its original version author rather than relabeling it now.


def snapshot(value, kind):
    common(value)
    require(value['archived'] is False)
    if kind != 'material':
        require('publication' in value and (value['publication'] is None or isinstance(value['publication'], dict)))
    timestamp(value['createdAt'])
    timestamp(value['updatedAt'])
    version = value['latestVersion']
    require(isinstance(version, dict))
    identity(version['id'])
    positive(version['number'])
    require(value['version'] >= version['number'])
    timestamp(version['createdAt'])
    provenance(version)
    if kind == 'post':
        from .posts import PostText
        from .post_blocks import text_projection, format_summary
        require(version['postId'] == value['id'])
        PostText.model_validate({key: version[key] for key in PostText.model_fields})
        text(version['title'], 300, nonempty=True)
        text(version['slug'], 100, nonempty=True)
        require(version['textProjection'] == text_projection(version['content']))
        require(type(version['formatSummary']['lossy']) is bool)
        require(version['formatSummary'] == format_summary(version['content']))
        text(version['markdownProjection'], 10_000_000)
        for key in ('documentIds', 'materialIds'):
            require(isinstance(version[key], list))
            require(len(version[key]) == len({identity(item) for item in version[key]}))
        require(version['coverImageId'] is None or identity(version['coverImageId']))
        sources = version['materialSources']
        require(isinstance(sources, list) and len(sources) == len(version['materialIds']))
        for source, material in zip(sources, version['materialIds']):
            require(isinstance(source, dict) and source['materialId'] == material)
            require(isinstance(source['documentIds'], list))
            for document in source['documentIds']:
                require(identity(document) in version['documentIds'])
        require(isinstance(version['conversionWarnings'], list))
        for warning in version['conversionWarnings']:
            require(isinstance(warning, dict) and set(warning) == {'code'}
                    and warning['code'] == 'code_language_downgraded')
        require(isinstance(value['author'], dict))
        text(value['author']['login'], 100, nonempty=True)
        title = version['title']
    else:
        from .research import MaterialInput, DocumentInput
        require(value['type'] == kind)
        model = MaterialInput if kind == 'material' else DocumentInput
        model.model_validate({key: value[key] for key in model.model_fields})
        title = text(value['title'], 300, nonempty=True)
        require(title == title.strip())
    summary = value['saved']
    require(isinstance(summary, dict) and set(summary) == {
        'resourceType', 'id', 'title', 'versionId', 'versionNumber', 'destination'})
    require(summary['resourceType'] == kind and summary['id'] == value['id'] and summary['title'] == title)
    identity(summary['id'])
    identity(summary['versionId'])
    positive(summary['versionNumber'])
    require(summary['versionId'] == version['id'] and summary['versionNumber'] == version['number'])
    require(summary['destination'] == {'path': '/write/' if kind == 'post' else f'/research/{kind}/',
                                      'resourceId': value['id'], 'versionId': version['id']})


def relation(value):
    common(value)
    timestamp(value['createdAt'])
    timestamp(value['updatedAt'])
    text(value['kind'], 80, nonempty=True)
    require(value['kind'] == value['kind'].strip())
    text(value['description'], 4000)
    require(type(value['directed']) is bool and 'saved' not in value)
    for endpoint in (value['source'], value['target']):
        require(isinstance(endpoint, dict) and endpoint['type'] in {'material', 'document'})
        identity(endpoint['id'])
        text(endpoint['title'], 300, nonempty=True)
    require(value['source']['id'] != value['target']['id'])


def success(name, value, arguments=None):
    """Return False for malformed or mismatched evidence; never infer a save.

    Without arguments validate the full response shape and internal ID relations.
    With arguments also check the normalized target/CAS/payload relationships.
    """
    try:
        from .research_tools import TOOLS
        require(name in TOOLS and 'mutationId' in TOOLS[name][0].model_fields)
        body = TOOLS[name][0].model_validate(arguments) if arguments is not None else None
        if name == 'research_archive':
            common(value)
            require(set(value) == {'id', 'archived', 'version'} and value['archived'] is True)
        elif name.startswith('research_relation_'):
            relation(value)
            if name.endswith('_save'):
                require(value['version'] == 1)
            if body is not None:
                require(value['kind'] == body.kind and value['description'] == body.description)
                if name.endswith('_save'):
                    endpoints = [(entry['type'], entry['id']) for entry in (value['source'], value['target'])]
                    expected = [(entry.type, entry.id) for entry in (body.source, body.target)]
                    require(endpoints == expected if body.directed else sorted(endpoints) == sorted(expected))
                    require(value['directed'] is body.directed)
        else:
            kind = name.split('_')[1]
            snapshot(value, kind)
            target = getattr(body, 'postId', None) if kind == 'post' else getattr(body, 'id', None)
            if name.endswith('_save') and target is None and (body is not None or kind != 'post'):
                require(value['version'] == 1 and value['latestVersion']['number'] == 1)
            if body is not None:
                saved_title = value['latestVersion']['title'] if kind == 'post' else value['title']
                require(saved_title == body.title)
                if kind != 'post':
                    require(value['content'] == body.content)
                if kind == 'material':
                    require(value['sourceUrl'] == body.sourceUrl and value['contentKind'] == body.contentKind)
                    require(timestamp(value['collectedAt']) == timestamp(body.collectedAt))
                if kind == 'post':
                    for key in ('description', 'tags', 'coverImageId', 'documentIds'):
                        require(value['latestVersion'][key] == getattr(body, key))
                    if body.materialIds is not None:
                        require(value['latestVersion']['materialIds'] == body.materialIds)
                    if body.postId and body.slug:
                        require(value['latestVersion']['slug'] == body.slug)
                if target is not None:
                    require(value['id'] == str(UUID(target)))
        if body is not None:
            expected = getattr(body, 'expectedVersion', None)
            if expected is not None:
                require(value['version'] == expected + 1)
            if hasattr(body, 'id'):
                require(value['id'] == str(UUID(body.id)))
        return True
    except Exception:
        return False


def failure(value, codes):
    try:
        require(isinstance(value, dict) and set(value) == {'error'})
        error = value['error']
        require(isinstance(error, dict) and {'code', 'message'} <= set(error) <= {'code', 'message', 'details'})
        require(isinstance(error['code'], str) and error['code'] in codes)
        text(error['message'], 1000, nonempty=True)
        require('details' not in error or isinstance(error['details'], dict))
        return True
    except Exception:
        return False
