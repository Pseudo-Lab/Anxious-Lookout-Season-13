"""Explicit one-file source diagnosis/export; Docker only, no discovery or refresh.

--export stdout is a credential transport, never a report/log. Pipe directly to the
reviewed runner-local supply client using verified Kubernetes TLS exec (no TTY).
"""
import argparse
import json
import os
import stat
import sys
from pathlib import Path

from app.codex_policy import CodexFailure
from runner.access import claims
from runner.auth import read_private
from runner.binding import canonical_uuid


class SourceFailure(CodexFailure):
    def __init__(self, reason, diagnostic):
        super().__init__(reason)
        self.diagnostic = diagnostic


def selected_file(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve() or path.is_symlink():
        raise SourceFailure('policy_refused', 'source_not_canonical_regular_private_file')
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or info.st_nlink != 1 or info.st_size > 1_000_000):
            raise SourceFailure('policy_refused', 'source_owner_mode_links_or_size_refused')
        raw = source.read(1_000_001)
        after_fd = os.fstat(source.fileno())
    after = path.lstat()
    def identity(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_uid, value.st_mode, value.st_nlink
    if len(raw) > 1_000_000 or not identity(before) == identity(info) == identity(after_fd) == identity(after):
        raise SourceFailure('policy_refused', 'source_changed_during_read')
    try:
        cache = json.loads(raw)
        if cache.get('OPENAI_API_KEY') or cache.get('auth_mode', 'chatgpt') != 'chatgpt':
            raise SourceFailure('policy_refused', 'unsupported_auth_mode_or_api_key')
        tokens = cache.get('tokens')
        if not isinstance(tokens, dict) or not isinstance(tokens.get('access_token'), str):
            raise SourceFailure('policy_refused', 'missing_access_token')
        try:
            claim, account = claims(tokens['access_token'])
        except CodexFailure as failure:
            raise SourceFailure(failure.reason, 'access_expired_or_under_120s' if failure.reason == 'auth_expired' else 'invalid_access_jwt') from None
        if tokens['account_id'] != account:
            raise SourceFailure('policy_refused', 'account_claim_mismatch')
        return tokens['access_token'], account, claim['exp']
    except CodexFailure:
        raise
    except Exception:
        raise SourceFailure('policy_refused', 'unsupported_source_json_or_missing_account') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Exactly operator-selected canonical file')
    parser.add_argument('--inputs', help='Private ownerId/stateId/windowId/newWindow JSON; contains no tokens')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--diagnose', action='store_true')
    mode.add_argument('--export', action='store_true')
    args = parser.parse_args()
    try:
        token, account, expiry = selected_file(args.source)
        if args.diagnose:
            print(json.dumps({'eligibleAccessSnapshot': True, 'format': 'chatgpt-jwt-access',
                              'expiryPresent': True, 'accountClaimMatchesSelectedFile': True,
                              'signatureOrEntitlementVerified': False, 'sourceModified': False,
                              'refreshOrIdTokenExported': False}))
        else:
            if not args.inputs:
                raise CodexFailure('policy_refused')
            value = read_private(Path(args.inputs))
            if set(value) != {'ownerId', 'stateId', 'windowId', 'newWindow'} or type(value['newWindow']) is not bool:
                raise CodexFailure('policy_refused')
            for field in ('ownerId', 'stateId', 'windowId'):
                canonical_uuid(value[field])
            print(json.dumps({'version': 1, **value, 'accountId': account, 'accessToken': token,
                              'expiresAt': expiry, 'maxDispatches': 3}))
    except Exception as failure:
        reason = str(failure) if isinstance(failure, CodexFailure) else 'codex_policy_refused'
        print(json.dumps({'eligibleAccessSnapshot': False, 'error': reason,
                          'diagnostic': getattr(failure, 'diagnostic', 'source_or_operator_input_refused')}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
