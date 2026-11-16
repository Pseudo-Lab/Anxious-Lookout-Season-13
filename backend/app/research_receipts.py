"""Server-owned safe receipts; stdout and model JSON never create these rows."""
from datetime import datetime, timezone
import uuid

from .research import fail, stamp
from .research_credentials import redact
from .research_results import success
from .research_tools import TOOLS

MAX_CALLS = 100
MUTATIONS = frozenset(name for name, (model, _) in TOOLS.items() if 'mutationId' in model.model_fields)
ERRORS = frozenset({'validation_error', 'conflict', 'not_found', 'forbidden', 'idempotency_conflict',
    'storage_quota_exceeded', 'content_limit_exceeded', 'payload_too_large', 'duplicate',
    'research_token_expired', 'research_turn_inactive', 'research_authorization_stale', 'not_ready'})


def ensure_capacity(row, request_id):
    count = sum(item.get('requestId') == str(request_id) for item in row.native_record.get('researchCalls', []))
    if count >= MAX_CALLS:
        fail('research_call_limit', 'This turn reached its research call limit', 429)


def receipt(name, result, arguments, request_id, *, code=None, turn_id=None):
    if name not in TOOLS or (code is not None and code not in ERRORS):
        raise ValueError('Invalid receipt category')
    value = {'id': str(uuid.uuid4()), 'requestId': str(request_id), 'operation': name,
             'status': 'failed' if code else 'succeeded', 'at': stamp(datetime.now(timezone.utc))}
    if turn_id:
        value['turnId'] = turn_id
    if code:
        return {**value, 'code': code}
    if name in MUTATIONS:
        if not success(name, result, arguments):
            fail('not_ready', 'Research API could not validate its result', 503)
        if 'saved' in result:
            value['saved'] = redact(result['saved'])
        else:
            value['target'] = {'type': 'relation' if name.startswith('research_relation_') else arguments['type'],
                               'id': result['id'], 'version': result['version']}
    else:
        target = {key: arguments[key] for key in ('type', 'id', 'versionId', 'postId', 'draftId', 'materialId')
                  if key in arguments and arguments[key] is not None}
        # Do not copy arbitrary strings from even a failed/forged lookup into history.
        for key, item in target.items():
            if key == 'type':
                if item not in {'material', 'document', 'post', 'relation'}:
                    raise ValueError('Invalid receipt target')
            else:
                target[key] = str(uuid.UUID(item))
        value['target'] = target
        latest = result.get('latestVersion') if isinstance(result, dict) else None
        if isinstance(latest, dict) and isinstance(latest.get('id'), str):
            value['target']['versionId'] = str(uuid.UUID(latest['id']))
        if isinstance(result, dict) and type(result.get('version')) is int and result['version'] > 0:
            value['target']['version'] = result['version']
        if isinstance(result, dict) and isinstance(result.get('items'), list):
            value['count'] = len(result['items'])
    return value


def append(row, value):
    ensure_capacity(row, value['requestId'])
    row.native_record = {**row.native_record,
                         'researchCalls': row.native_record.get('researchCalls', []) + [value]}


def display(row):
    """Receipts were produced only by this API, kept outside native snapshots."""
    return redact(row.native_record.get('researchCalls', []))
