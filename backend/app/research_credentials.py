"""Turn-only public research principals. No provider, callback or PAT authority."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid

from .research import fail, stamp
from .page_scope import shell_allowed

PURPOSE = 'research-public-v1'
TOKEN = re.compile(r'rturn1\.[0-9a-f-]{36}\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}')
PAT_TOKEN = re.compile(r'rpat1\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}')
CREDENTIAL_TOKEN = re.compile('(?:' + TOKEN.pattern + '|' + PAT_TOKEN.pattern + ')')


def pat_partial_start(value):
    """Earliest incomplete PAT suffix, at most85 chars; no secret state."""
    literal = 'rpat1.'
    for start in range(max(0, len(value) - 85), len(value)):
        suffix = value[start:]
        if len(suffix) <= 6:
            if literal.startswith(suffix):
                return start
            continue
        if not suffix.startswith(literal):
            continue
        rest = suffix[6:]
        if (re.fullmatch(r'[0-9a-f-]{0,36}', rest)
                or (len(rest) >= 37 and re.fullmatch(r'[0-9a-f-]{36}\.[A-Za-z0-9_-]{0,42}', rest))):
            return start
    return len(value)


def enabled(name='RESEARCH_AGENT_API_ENABLE'):
    value = os.getenv(name, 'false')
    if value not in {'true', 'false'}:
        raise ValueError(name + ' must be true or false')
    return value == 'true'


def redact(value):
    """Also mask retired well-formed tokens when only their hashes remain."""
    if isinstance(value, str):
        return CREDENTIAL_TOKEN.sub('[REDACTED]', value)
    if isinstance(value, dict):
        return {redact(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def reject_token_input(value):
    if redact(value) != value:
        fail('validation_error', 'Credential text is not accepted as research content', 422)


def canonical(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Invalid identity')
    return value


def token_hash(token):
    return hashlib.sha256((PURPOSE + '\0' + token).encode()).hexdigest()


def scope_hash(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def locate(token):
    try:
        if not isinstance(token, str) or TOKEN.fullmatch(token) is None:
            raise ValueError()
        _, conversation, request, _ = token.split('.')
        return uuid.UUID(canonical(conversation)), uuid.UUID(canonical(request))
    except (ValueError, TypeError, AttributeError):
        fail('unauthenticated', 'Research authentication required', 401)


def issue(conversation, request, scope, login_expiry, now=None, *, owner):
    if not shell_allowed(scope):
        fail('tool_not_allowed', 'Research API is unavailable in this mode', 403)
    now = now or datetime.now(timezone.utc)
    expiry = min(now + timedelta(minutes=30), login_expiry)
    if expiry <= now:
        fail('unauthenticated', 'Research authentication required', 401)
    token = 'rturn1.' + canonical(str(conversation)) + '.' + canonical(str(request)) + '.' + secrets.token_urlsafe(32)
    return token, {'id': str(uuid.uuid4()), 'ownerId': canonical(str(owner)), 'purpose': PURPOSE, 'hash': token_hash(token),
                   'expiresAt': stamp(expiry), 'scopeHash': scope_hash(scope)}


@dataclass(frozen=True)
class Principal:
    owner: uuid.UUID
    conversation: uuid.UUID
    request: uuid.UUID
    credential_id: str


def resolve(row, token, metadata, now=None):
    """Call again on fresh locked data, including before an idempotent replay."""
    conversation, request = locate(token)
    grant = (metadata or {}).get('researchGrant') or {}
    try:
        matches = (row.id == conversation and row.request_id == request and not row.archived
                   and grant['ownerId'] == str(row.owner_id)
                   and metadata['requestId'] == str(request) and grant['purpose'] == PURPOSE
                   and isinstance(grant['hash'], str) and hmac.compare_digest(grant['hash'], token_hash(token))
                   and shell_allowed(metadata['scope'])
                   and hmac.compare_digest(grant['scopeHash'], scope_hash(metadata['scope'])))
        expiry = datetime.fromisoformat(grant['expiresAt'].replace('Z', '+00:00'))
        canonical(grant['id'])
        if not matches or expiry.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        fail('unauthenticated', 'Research authentication required', 401)
    if (now or datetime.now(timezone.utc)) >= expiry:
        fail('research_token_expired', 'Research access expired; start a new turn', 401)
    if row.state != 'running':
        fail('research_turn_inactive', 'Research turn is no longer active', 401)
    return Principal(row.owner_id, conversation, request, grant['id'])


def revoke(row):
    record = dict(row.native_record)
    if not any((entry.get('researchGrant') or {}).get('hash') is not None
               for entry in record.get('platformRequests', [])):
        return
    record['platformRequests'] = [{**entry, 'researchGrant': {**entry['researchGrant'], 'hash': None}}
                                 if entry.get('researchGrant') else entry
                                 for entry in record.get('platformRequests', [])]
    row.native_record = record


def revoke_sessions(db, *, owner=None, login_hash=None):
    """Trusted logout/role/restore paths; serialize grant removal on each row."""
    from sqlalchemy import text
    if db.execute(text("SELECT to_regclass('research.conversations')")).scalar_one() is None:
        return
    predicates = []
    values = {}
    if owner is not None:
        predicates.append('owner_id=:owner')
        values['owner'] = owner
    if login_hash is not None:
        predicates.append('login_hash=:login_hash')
        values['login_hash'] = login_hash
    predicates.append("""EXISTS (SELECT 1 FROM jsonb_array_elements(
        CASE WHEN jsonb_typeof(native_record->'platformRequests')='array'
        THEN native_record->'platformRequests' ELSE '[]'::jsonb END) entry
        WHERE entry->'researchGrant'->>'hash' IS NOT NULL)""")
    db.execute(text("""WITH locked AS (
        SELECT id FROM research.conversations WHERE """ + ' AND '.join(predicates) + """ ORDER BY id FOR UPDATE
    ) UPDATE research.conversations c SET tool_token_hash=NULL, tool_expires_at=NULL,
        native_record=jsonb_set(c.native_record,'{platformRequests}',
            (SELECT jsonb_agg(CASE WHEN entry ? 'researchGrant'
                THEN jsonb_set(entry,'{researchGrant,hash}','null'::jsonb) ELSE entry END ORDER BY ordinal)
             FROM jsonb_array_elements(c.native_record->'platformRequests') WITH ORDINALITY AS items(entry,ordinal)))
    FROM locked WHERE c.id=locked.id"""), values)
