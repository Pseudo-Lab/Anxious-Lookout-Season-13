"""API-controlled terminal decision, never a storage/tool replay.

The caller obtains the descriptor only from an authenticated bound runner read
and verifies it against the original owned conversation/request first. Commit
this independent transaction before sending any ACK to that runner.
"""
import hmac
import re
import uuid

from sqlalchemy import text

from .research import mutation_lock, parse_id
from .research_models import Idempotency
from .page_scope import POLICIES, request_metadata
from .research_tools import TOOLS


NOT_APPLIED = {'error': {'code': 'mutation_not_applied',
                         'message': 'The original storage operation was not applied'}}
MUTATIONS = frozenset(name for name, (model, _) in TOOLS.items() if 'mutationId' in model.model_fields)
BINDING = frozenset({'ownerId', 'stateId', 'sessionId', 'requestId', 'mutationId',
                     'tool', 'fingerprint', 'fenceId', 'native'})


def verified(owner, row, result):
    value = result.get('researchFence')
    if not isinstance(value, dict) or set(value) != BINDING | {'version', 'phase', 'outcome'}:
        return None
    try:
        for field in ('ownerId', 'stateId', 'sessionId', 'requestId', 'mutationId', 'fenceId'):
            if not isinstance(value[field], str) or str(uuid.UUID(value[field])) != value[field]:
                return None
        if (type(value['version']) is not int or value['version'] != 1
                or value['ownerId'] != str(owner) or value['ownerId'] != result.get('ownerId')
                or value['stateId'] != result.get('stateId') or value['sessionId'] != str(row.id)
                or value['requestId'] != str(row.request_id) or value['requestId'] != result.get('requestId')
                or value['tool'] not in MUTATIONS or not isinstance(value['fingerprint'], str)
                or re.fullmatch('[0-9a-f]{64}', value['fingerprint']) is None):
            return None
        native = value['native']
        if (not isinstance(native, dict) or set(native) != {'generation', 'threadId', 'turnId', 'itemId'}
                or any(not isinstance(part, str) or not part or len(part) > 200 for part in native.values())
                or native['threadId'] != result.get('threadId') or native['turnId'] != result.get('turnId')):
            return None
        metadata = request_metadata(row, value['requestId'])
        if (metadata is None or value['tool'] not in POLICIES.get(metadata['scope']['page']['kind'], (None, []))[1]
                or (metadata.get('turnId') is not None and metadata['turnId'] != native['turnId'])):
            return None
        if (value['phase'], value['outcome']) not in {('execute_intent', None),
                ('terminal', 'applied'), ('terminal', 'not_applied')}:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return value


def reconcile(sessions, owner, row, runner, result):
    """Called by ordinary owned polling only, never the stream or tool callback."""
    value = verified(owner, row, result)
    if value is None:
        return 'unknown'
    if value['phase'] == 'terminal':
        # The protected runner already durably recorded the original callback's
        # terminal proof (or an earlier committed API settlement ACK).
        return value['outcome']
    if result.get('researchDrained') is not True or result.get('state') not in {'idle', 'failed'}:
        return 'checking'
    try:
        outcome = resolve(sessions, owner, value['mutationId'], value['fingerprint'])
        if outcome not in {'applied', 'not_applied'}:
            return outcome
        reply = runner.research_settlement(row.id,
            {'binding': {key: value[key] for key in BINDING}, 'outcome': outcome,
             'podUid': result.get('podUid')})
        if (reply.get('requestId') != value['requestId'] or reply.get('fenceId') != value['fenceId']
                or reply.get('outcome') != outcome):
            return 'checking'
        return outcome
    except Exception:
        # This includes commit failure and lost ACKs. Only status/ACK are retried
        # on the next owned poll; no content action or model is ever replayed.
        return 'checking'


def decide(db, owner, mutation_id, fingerprint):
    identity = parse_id(mutation_id)
    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:lock)'),
                     {'lock': mutation_lock(owner, identity)}):
        return 'checking'
    stored = db.get(Idempotency, (owner, identity))
    if stored is not None:
        if not hmac.compare_digest(stored.fingerprint, fingerprint):
            return 'unknown'
        if 200 <= stored.status < 300 and isinstance(stored.response, dict):
            return 'applied'
        if stored.status == 409 and stored.response == NOT_APPLIED:
            return 'not_applied'
        return 'unknown'
    db.add(Idempotency(owner_id=owner, key=identity, fingerprint=fingerprint,
                       status=409, response=NOT_APPLIED))
    db.flush()
    return 'not_applied'


def resolve(sessions, owner, mutation_id, fingerprint):
    with sessions.begin() as db:
        outcome = decide(db, owner, mutation_id, fingerprint)
    # Exiting begin is the commit boundary; its exceptions prevent ACK dispatch.
    return outcome
