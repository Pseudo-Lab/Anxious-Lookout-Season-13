"""Write-ahead mutation fence on the existing private durable control volume.

No network, credentials or mutation replay live here. A terminal transition must
be called only after the bound API has committed its matching terminal result.
Native completion/drain is an independent admission condition.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import threading
import uuid

from .auth import private_directory, write_private
from .research_authority import AuthorityError, strict_json
from .service_store import private_file


MUTATIONS = frozenset({'research_material_save', 'research_document_save',
    'research_post_save', 'research_material_update', 'research_document_update',
    'research_relation_save', 'research_relation_update', 'research_archive'})
BINDING = frozenset({'ownerId', 'stateId', 'sessionId', 'requestId', 'mutationId',
                     'tool', 'fingerprint', 'fenceId', 'native'})
FIELDS = BINDING | {'version', 'phase', 'outcome'}


def canonical_uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise AuthorityError('fence_identity')
    return value


def validate(value, owner, state):
    if not isinstance(value, dict) or set(value) != FIELDS or type(value['version']) is not int or value['version'] != 1:
        raise AuthorityError('fence_shape')
    for field in ('ownerId', 'stateId', 'sessionId', 'requestId', 'mutationId', 'fenceId'):
        canonical_uuid(value[field])
    if value['ownerId'] != owner or value['stateId'] != state:
        raise AuthorityError('fence_binding')
    native = value['native']
    if (not isinstance(native, dict) or set(native) != {'generation', 'threadId', 'turnId', 'itemId'}
            or any(not isinstance(part, str) or not part or len(part) > 200 for part in native.values())
            or value['tool'] not in MUTATIONS or not isinstance(value['fingerprint'], str)
            or re.fullmatch('[0-9a-f]{64}', value['fingerprint']) is None):
        raise AuthorityError('fence_operation')
    if (value['phase'], value['outcome']) not in {('execute_intent', None),
                                                ('terminal', 'applied'), ('terminal', 'not_applied')}:
        raise AuthorityError('fence_phase')
    return value


class MutationFence:
    def __init__(self, control, owner, state):
        self.control = private_directory(Path(control))
        self.owner, self.state = canonical_uuid(owner), canonical_uuid(state)
        self.path = self.control / 'research-fence.json'
        self.lock = threading.Lock()

    @contextmanager
    def locked(self):
        # Native's volume lease is still required. This separate lock orders
        # callback publication against polling ACKs and protects atomic replace.
        with self.lock:
            private_directory(self.control)
            descriptor = private_file(self.control / 'research-fence.lock', create=True)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                os.close(descriptor)

    def _read(self):
        if not os.path.lexists(self.path):
            return None
        descriptor = private_file(self.path)
        try:
            size = os.fstat(descriptor).st_size
            if size > 8192:
                raise AuthorityError('fence_size')
            data = os.read(descriptor, 8193)
        finally:
            os.close(descriptor)
        return validate(strict_json(data), self.owner, self.state)

    def status(self):
        with self.locked():
            value = self._read()
            if value and value['phase'] == 'terminal':
                # A prior ACK may have failed after replace but before dir fsync.
                # Do not turn that in-memory visibility into admission evidence.
                self._sync_terminal()
            return value

    def pending(self):
        value = self.status()
        return value if value and value['phase'] == 'execute_intent' else None

    def _sync_terminal(self):
        descriptor = private_file(self.path)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(self.control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def begin(self, work, mutation_id, fingerprint):
        value = {'version': 1, 'ownerId': self.owner, 'stateId': self.state,
                 'sessionId': work.session, 'requestId': work.request,
                 'mutationId': str(uuid.UUID(mutation_id)), 'tool': work.call.name,
                 'fingerprint': fingerprint, 'fenceId': str(uuid.uuid4()),
                 'native': {'generation': work.generation, 'threadId': work.call.thread,
                            'turnId': work.call.turn, 'itemId': work.call.item},
                 'phase': 'execute_intent', 'outcome': None}
        validate(value, self.owner, self.state)
        with self.locked():
            prior = self._read()
            if prior is not None and prior['phase'] != 'terminal':
                raise AuthorityError('mutation_unsettled')
            # write_private performs file fsync -> atomic replace -> dir fsync.
            # The caller may send HTTP only after this returns successfully.
            write_private(self.path, value)
        return value

    def terminal(self, binding, outcome):
        """Exact ACK, never 'clear latest'. Retain terminal receipt for lost ACKs."""
        if not isinstance(binding, dict) or set(binding) != BINDING or outcome not in {'applied', 'not_applied'}:
            raise AuthorityError('settlement_shape')
        with self.locked():
            value = self._read()
            if value is None or any(value[key] != binding[key] for key in BINDING):
                raise AuthorityError('settlement_mismatch')
            if value['phase'] == 'terminal':
                if value['outcome'] != outcome:
                    raise AuthorityError('settlement_mismatch')
                self._sync_terminal()
                return value
            value.update(phase='terminal', outcome=outcome)
            # Persist the committed result instead of unlinking the only receipt.
            # `pending` becomes empty only after the full durable write returns.
            write_private(self.path, value)
            return value
