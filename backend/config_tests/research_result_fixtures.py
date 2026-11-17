"""Full tool results from production serializers over fake stored rows; no DB."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.post_models import PostVersion
from app.research import Store
from app.research_models import ContentVersion
from app.version_authors import saved


def save_result(kind='document', arguments=None, conversation=None):
    args = {'title': 'document', 'content': '# saved', **(arguments or {})}
    owner, conversation = uuid4(), UUID(conversation) if conversation else uuid4()
    resource_id = UUID(args.get('id') or args.get('postId') or str(uuid4()))
    version = (args.get('expectedVersion') or 0) + 1
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    item = SimpleNamespace(id=resource_id, kind=kind, version=version, content_number=min(version, 2),
                           archived=False, created_at=now, updated_at=now)
    latest = SimpleNamespace(id=uuid4(), post_id=resource_id, number=item.content_number,
        title=args['title'].strip() if kind != 'post' else args['title'], content=args['content'],
        created_at=now, author_kind='codex', author_conversation_id=conversation)
    if kind == 'post':
        from app.posts import PostMetadata, post_value
        from app.post_block_markdown import convert
        metadata = {'description': '', 'slug': 'post-fixture', 'tags': [], 'coverImageId': None,
                    'documentIds': [], 'materialIds': [], **args}
        metadata['materialIds'] = metadata['materialIds'] or []
        document, warnings = convert(args.get('contentInput', {'text': '# saved'})['text'], 'fixture')
        latest.payload = {key: metadata[key] for key in PostMetadata.model_fields}
        latest.payload.update(content=document, conversionWarnings=warnings,
            materialSources=[{'materialId': identity, 'documentIds': []} for identity in metadata['materialIds']])
    else:
        latest.source_url = args.get('sourceUrl', 'https://example.invalid/source')
        latest.collected_at = datetime.fromisoformat(args.get('collectedAt', now.isoformat()).replace('Z', '+00:00'))
        latest.content_kind = args.get('contentKind', 'excerpt')
    class Database:
        def scalar(self, query):
            entity = query.column_descriptions[0].get('entity')
            return latest if entity in {ContentVersion, PostVersion} else None
        def execute(self, *_):
            return SimpleNamespace(scalar_one=lambda: '0009_research_pats')
        def get(self, *_):
            return SimpleNamespace(login='fixture-owner')
    store = Store(Database(), owner)
    resource = post_value(store, item) if kind == 'post' else store.serialize_item(item)
    return saved(resource, kind)


def relation_result(arguments, *, update=False):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    endpoints = arguments.get('source', {'type': 'document', 'id': str(uuid4())}), arguments.get('target', {'type': 'material', 'id': str(uuid4())})
    directed = arguments.get('directed', True)
    if not directed:
        endpoints = sorted(endpoints, key=lambda endpoint: endpoint['id'])
    items = {endpoint['id']: SimpleNamespace(id=UUID(endpoint['id']), kind=endpoint['type'], title='endpoint') for endpoint in endpoints}
    row = SimpleNamespace(id=UUID(arguments['id']) if update else uuid4(), source_id=endpoints[0]['id'],
        target_id=endpoints[1]['id'], kind=arguments['kind'].strip(), description=arguments.get('description', ''),
        directed=directed, version=arguments.get('expectedVersion', 0) + 1, created_at=now, updated_at=now)
    store = SimpleNamespace(owned_item=lambda identity: items[identity],
                            serialize_item=lambda item, _: {'title': item.title})
    return Store.serialize_relation(store, row)
