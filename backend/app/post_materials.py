"""Explicit post source selection; never copy source bodies into publications."""
from fastapi import Request
from pydantic import Field, field_validator
from sqlalchemy import select
from .research import Input, fail, parse_id
from .research_models import Item, ContentVersion, Relation


class Resolve(Input):
    documentIds: list[str] = Field(max_length=50)

    @field_validator('documentIds')
    @classmethod
    def ids(cls, values):
        values = [str(parse_id(value)) for value in values]
        if len(set(values)) != len(values): raise ValueError('Duplicate documents')
        return values


def current(store, item):
    return store.db.scalar(select(ContentVersion).where(ContentVersion.item_id == item.id,
        ContentVersion.number == item.content_number, ContentVersion.owner_id == store.owner))


def option(store, item):
    version = current(store, item)
    return {'id': str(item.id), 'title': version.title, 'sourceUrl': version.source_url,
            'version': item.version, 'latestVersionId': str(version.id)}


def resolve(store, document_ids, enforce_limit=True):
    targets, excluded = {}, []
    for identity in document_ids:
        document = store.owned_item(identity, 'document', lock=True)
        if document.archived: fail('validation_error', 'Remove archived document references', 422)
        relations = store.db.scalars(select(Relation).where(Relation.owner_id == store.owner,
            Relation.source_id == document.id, Relation.source_kind == 'document',
            Relation.target_kind == 'material', Relation.archived.is_(False)).order_by(Relation.id)).all()
        for relation in relations:
            material = store.owned_item(relation.target_id, 'material', lock=True)
            key = str(material.id)
            if material.archived:
                if key not in excluded: excluded.append(key)
                continue
            if key not in targets: targets[key] = {**option(store, material), 'documentIds': []}
            if identity not in targets[key]['documentIds']: targets[key]['documentIds'].append(identity)
    if enforce_limit and len(targets) > 50: fail('validation_error', 'At most 50 source materials', 422)
    return {'items': list(targets.values()), 'excludedArchivedIds': excluded}


def select_sources(store, value):
    resolved = resolve(store, value['documentIds'], enforce_limit=False)
    origins = {item['id']: item['documentIds'] for item in resolved['items']}
    identities = value.get('materialIds')
    if identities is None: identities = list(origins)
    if len(identities) > 50: fail('validation_error', 'At most 50 source materials', 422)
    for identity in identities:
        row = store.owned_item(identity, 'material', lock=True)
        if row.archived: fail('validation_error', 'Remove archived material references before saving', 422)
    value['materialIds'] = identities
    value['materialSources'] = [{'materialId': identity, 'documentIds': origins.get(identity, [])} for identity in identities]


def register(app, authorized, settings, response):
    from . import posts
    root = settings.base_path + '/api/research/post-material-options'

    @app.get(root)
    def options(request: Request, limit: int = 30, cursor: str | None = None, q: str | None = None):
        with authorized(request) as store:
            posts.ready(store)
            query = select(Item).join(ContentVersion, (ContentVersion.item_id == Item.id) &
                (ContentVersion.number == Item.content_number)).where(Item.owner_id == store.owner,
                Item.kind == 'material', Item.archived.is_(False))
            query = posts.search(query, ContentVersion.title, q)
            return response(posts.listing(store, Item, query, lambda row: option(store, row), limit, cursor))

    @app.post(root + '/resolve')
    def references(body: Resolve, request: Request):
        with authorized(request, True) as store:
            posts.ready(store); store.graph_lock()
            return response(resolve(store, body.documentIds))
