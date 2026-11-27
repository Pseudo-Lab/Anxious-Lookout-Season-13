"""#119 I2/I3/J2: owner material/document lists with title search, sort orders and a total.

`sort=updated` keeps the original page_query cursor ([time, id]) so existing clients and cursors are unchanged.
Other orders use their own keyset cursor [sort, value, id]; a cursor is only valid with the sort that made it.
"""
import base64
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select

from .research_models import ContentVersion, Item

SORTS = ("updated", "created", "title", "domain")
# Host of an http(s)-style URL: scheme, optional userinfo, then the host up to port/path/query/fragment.
HOST = r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/?#]*@)?([^:/?#]+)"
LAST = "\U0010ffff"  # sorts after every host under COLLATE "C"; items without a URL go last


def fail(code, message, status):
    from .research import fail as research_fail
    research_fail(code, message, status)


def statement(owner, kind, archived, q):
    query = select(Item, ContentVersion).join(ContentVersion, and_(ContentVersion.item_id == Item.id,
        ContentVersion.number == Item.content_number, ContentVersion.owner_id == Item.owner_id)).where(
        Item.owner_id == owner, Item.kind == kind, Item.archived == archived)
    if q is not None:
        if not 1 <= len(q) <= 200 or "\x00" in q:
            fail("validation_error", "Invalid search query", 422)
        match = ContentVersion.title.icontains(q, autoescape=True)
        if kind == "material":
            match = or_(match, ContentVersion.source_url.icontains(q, autoescape=True))
        query = query.where(match)
    return query


def key(sort):
    """(column, descending) for a non-default sort; ties always break on Item.id in the same direction."""
    if sort == "created":
        return Item.created_at, True
    if sort == "title":
        return ContentVersion.title.collate("C"), False
    return func.coalesce(func.lower(func.substring(ContentVersion.source_url, HOST)), LAST).collate("C"), False


def decode(cursor, sort):
    try:
        if len(cursor) > 1024:
            raise ValueError()
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(values, list) or len(values) != 3 or values[0] != sort or not all(isinstance(v, str) for v in values):
            raise ValueError()
        _, value, identity = values
        if sort == "created":
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError()
            value = value.astimezone(timezone.utc)
        return value, uuid.UUID(identity)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        fail("validation_error", "Invalid cursor", 422)


def encode(sort, value, identity):
    if isinstance(value, datetime):
        from .research import stamp
        value = stamp(value)
    return base64.urlsafe_b64encode(json.dumps([sort, value, str(identity)]).encode()).decode().rstrip("=")


def page(db, owner, kind, limit, cursor, archived, q, sort):
    """Rows of (Item, ContentVersion), the next cursor and the total for the filters (cursor ignored)."""
    from .research import page_query, encode_cursor
    if sort not in SORTS or (sort == "domain" and kind != "material"):
        fail("validation_error", "Invalid sort", 422)
    base = statement(owner, kind, archived, q)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    if sort == "updated":
        rows = db.execute(page_query(base, Item, limit, cursor)).all()
        following = encode_cursor(rows[limit - 1][0]) if len(rows) > limit else None
        return rows[:limit], following, total
    if not 1 <= limit <= 100:
        fail("validation_error", "Limit must be between 1 and 100", 422)
    column, descending = key(sort)
    query = base.add_columns(column.label("sort_key"))
    if cursor:
        value, identity = decode(cursor, sort)
        after = (column < value) if descending else (column > value)
        tie = (Item.id < identity) if descending else (Item.id > identity)
        query = query.where(or_(after, and_(column == value, tie)))
    order = (column.desc(), Item.id.desc()) if descending else (column.asc(), Item.id.asc())
    rows = db.execute(query.order_by(*order).limit(limit + 1)).all()
    following = encode(sort, rows[limit - 1].sort_key, rows[limit - 1][0].id) if len(rows) > limit else None
    return [(row[0], row[1]) for row in rows[:limit]], following, total
