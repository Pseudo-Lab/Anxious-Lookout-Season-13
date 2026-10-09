"""Private preflight only: no network/cluster writes and no credential output."""
import argparse
import json
import os
import stat
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet


def private_file(root, relative):
    path = root / relative
    for directory in (root, path.parent):
        info = directory.lstat()
        if (directory != directory.resolve() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError()
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_size > 10000):
        raise ValueError()
    return path.read_text().strip()


def database_url(value, role):
    parsed = urlsplit(value)
    if (parsed.scheme != "postgresql+psycopg" or parsed.hostname != "postgres.codex-trial.svc.cluster.local"
            or parsed.port not in {None, 5432} or parsed.path != "/codex_trial"
            or parsed.username != role or not parsed.password or parsed.query or parsed.fragment):
        raise ValueError()
    return unquote(parsed.password)


def check(root):
    root = Path(root).absolute()
    admin_password = private_file(root, "db/password")
    api_password = private_file(root, "ops/api-password")
    if min(len(admin_password), len(api_password)) < 16 or admin_password == api_password:
        raise ValueError()
    if database_url(private_file(root, "ops/admin-url"), "postgres") != admin_password:
        raise ValueError()
    if database_url(private_file(root, "api/database-url"), "anxious_api") != api_password:
        raise ValueError()
    if len(private_file(root, "github/client-secret")) < 16:
        raise ValueError()
    Fernet(private_file(root, "github/transaction-key").encode())
    return {"status": "ok", "scope": "new-trial-db-only", "credentialsPrinted": False,
            "callbackRegistrationVerified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.directory)))
    except Exception:
        print("Trial private input preflight refused; credentials are not printed", file=sys.stderr)
        sys.exit(1)
