"""Same-UID local socket client: bounded private stdin, only safe receipt stdout."""
import json
import os
import socket
import sys
from pathlib import Path

from .access import MAX_ENVELOPE
from .auth import private_directory
from .binding import canonical_uuid


def main():
    try:
        if len(sys.argv) not in (2, 4):
            raise ValueError()
        if len(sys.argv) == 4:
            if sys.argv[2] != '--expected-pod-uid':
                raise ValueError()
            expected = canonical_uuid(sys.argv[3])
            actual = canonical_uuid(os.environ.get('RUNNER_POD_UID', ''))
            if expected != actual:
                raise ValueError()
        # Refuse a stale exec target before consuming credential stdin. The
        # host still checks registry generation/UID and serializes with stop.
        path = Path(sys.argv[1])
        private_directory(path.parent)
        payload = sys.stdin.buffer.read(MAX_ENVELOPE+1)
        if not payload or len(payload) > MAX_ENVELOPE:
            raise ValueError()
        envelope = json.loads(payload)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(60)
            client.connect(str(path))
            client.sendall(json.dumps(envelope).encode()+b'\n')
            client.shutdown(socket.SHUT_WR)
            with client.makefile('rb') as incoming:
                receipt = json.loads(incoming.readline(1000))
        if receipt == {'accepted': True}:
            print('{"accepted":true}')
            return 0
        # Do not trust arbitrary exception/socket payloads as printable errors.
        if receipt.get('error') not in {'codex_auth_expired', 'codex_auth_revoked', 'codex_model_unavailable',
                                        'codex_policy_refused', 'codex_unavailable'}:
            raise ValueError()
        print(json.dumps({'accepted': False, 'error': receipt['error']}))
    except Exception:
        print('{"accepted":false,"error":"codex_unavailable","receipt":"unknown"}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
