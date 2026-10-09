"""Explicit disposable account mapping; no provider/real credential imports."""
import argparse
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.offline_services import require_offline


def configure(url, output, account_a, account_b):
    require_offline()
    first, second = uuid.UUID(str(account_a)), uuid.UUID(str(account_b))
    if first == second or make_url(url).database != "hosting_test":
        raise ValueError("Two distinct disposable integration accounts are required")
    root = Path(output)
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise ValueError("A new empty fixture directory is required")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as db:
            accounts = db.execute(text("SELECT id,is_approved FROM auth.accounts WHERE id IN (:first,:second)"),
                                  {"first": first, "second": second}).all()
            if len(accounts) != 2 or not all(row.is_approved for row in accounts):
                raise ValueError("Both accounts must exist and be approved in the fixture DB")
    finally:
        engine.dispose()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    mapping = {}
    for label, owner in (("a", first), ("b", second)):
        token_path = root / f"runner-{label}.token"
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(secrets.token_urlsafe(32))
        mapping[str(owner)] = {"url": f"http://offline-{label}:8080", "tokenFile": f"/run/fixtures/runner-{label}.token"}
    descriptor = os.open(root / "runners.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(mapping, file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate new isolated offline fixture mapping for two verified approved account UUIDs")
    parser.add_argument("--account-a", required=True)
    parser.add_argument("--account-b", required=True)
    parser.add_argument("--output", default="/fixture")
    args = parser.parse_args()
    try:
        configure(os.environ["DATABASE_URL"], args.output, args.account_a, args.account_b)
    except Exception:
        print("Offline mapping setup failed; account/credential/SQL details are not printed", file=sys.stderr)
        sys.exit(1)
    print("PASS offline fixture mapping created; separate tokens, no model credentials")
