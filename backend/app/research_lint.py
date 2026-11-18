"""research_lint (#105 C.2): deterministic, read-only checks of the owner's active wiki graph.

Only what the server can decide exactly is checked here; semantic contradictions
are the skill's (LLM's) job. Archived items and relations are ignored, as are
relations with an archived endpoint (they are not part of the visible graph).
"""
from datetime import datetime, timezone

from sqlalchemy import and_, select

from .bibliography import missing_fields
from .research import stamp
from .research_models import ContentVersion, Item, Relation

CODES = ("orphan_material", "missing_bibliography", "relation_without_reason",
         "support_refute_conflict", "cites_superseded")


def lint(store, limit=200):
    # One consistent owner-graph snapshot (review r1 major): every graph write
    # (item create/change/archive, relation create/change/archive) takes this
    # transaction lock, so none can commit between the reads below.
    store.graph_lock()
    rows = store.db.execute(select(Item, ContentVersion).join(
        ContentVersion, and_(ContentVersion.item_id == Item.id, ContentVersion.number == Item.content_number,
                             ContentVersion.owner_id == Item.owner_id))
        .where(Item.owner_id == store.owner, Item.archived.is_(False))).all()
    items = [item for item, _ in rows]
    latest = {item.id: version for item, version in rows}
    relations = store.db.scalars(select(Relation).where(*store.active_relations())).all()
    return check(items, latest, relations, limit)


def check(items, latest, relations, limit):
    """Pure over loaded rows: active items, their latest versions, visible relations."""
    ref = {item.id: {"type": item.kind, "id": str(item.id), "title": latest[item.id].title}
           for item in items if item.id in latest}
    # Defensive: only relations whose endpoints are both in this snapshot.
    relations = sorted((row for row in relations if row.source_id in ref and row.target_id in ref),
                       key=lambda row: str(row.id))
    found = {code: [] for code in CODES}

    linked = {end for row in relations for end in (row.source_id, row.target_id)}
    for item in sorted(items, key=lambda row: str(row.id)):
        if item.kind != "material" or item.id not in ref:
            continue
        if item.id not in linked:
            found["orphan_material"].append({"code": "orphan_material", "item": ref[item.id]})
        missing = missing_fields(latest[item.id].bibliography)
        if missing:
            found["missing_bibliography"].append({"code": "missing_bibliography", "item": ref[item.id],
                                                  "missingFields": missing})

    for row in relations:
        if not row.description.strip():
            found["relation_without_reason"].append({"code": "relation_without_reason", "relation": {
                "id": str(row.id), "kind": row.kind, "source": ref[row.source_id], "target": ref[row.target_id]}})

    pairs = {}
    for row in relations:
        if row.kind in {"supports", "refutes"}:
            key = tuple(sorted((row.source_id, row.target_id), key=str))
            kinds, identities = pairs.setdefault(key, (set(), []))
            kinds.add(row.kind)
            identities.append(str(row.id))
    for key in sorted(pairs, key=lambda pair: (str(pair[0]), str(pair[1]))):
        kinds, identities = pairs[key]
        if kinds == {"supports", "refutes"}:
            found["support_refute_conflict"].append({"code": "support_refute_conflict", "items": [ref[end] for end in key],
                                                     "relationIds": sorted(identities)})

    superseded = {}
    for row in relations:
        if row.kind == "updates" and row.target_kind == "material":
            superseded.setdefault(row.target_id, set()).add(row.source_id)
    citations = [row for row in relations if row.source_kind == "document" and row.target_kind == "material"
                 and row.target_id in superseded]
    for row in sorted(citations, key=lambda row: (str(row.source_id), str(row.target_id), str(row.id))):
        newer = sorted(superseded[row.target_id] - {row.source_id}, key=str)
        if newer:
            found["cites_superseded"].append({"code": "cites_superseded", "document": ref[row.source_id],
                                              "material": ref[row.target_id], "supersededBy": [ref[end] for end in newer],
                                              "relationId": str(row.id)})

    every = [finding for code in CODES for finding in found[code]]
    return {"checkedAt": stamp(datetime.now(timezone.utc)), "counts": {code: len(found[code]) for code in CODES},
            "items": every[:limit], "truncated": len(every) > limit}
