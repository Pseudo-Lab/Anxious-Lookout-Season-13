"""Private loopback DB input copy and read-only preparation-retry audit.

Transport is a separately reviewed operator kubectl port-forward. Never change
production Secrets, print URLs or infer no DB commit from an empty output folder.
"""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.runner_schema import validate
from runner.auth import read_private
from runner.lifecycle import require


def stage(source, output, port):
    require(type(port) is int and 1024 <= port <= 65535)
    require(output.is_absolute() and output == output.resolve())
    urls = {}
    for filename, username in [('database-url', 'anxious_api'), ('admin-database-url', 'postgres')]:
        url = make_url(read_private(source/filename, json_format=False).strip())
        require(url.drivername == 'postgresql+psycopg' and url.username == username
                and url.host == 'postgres.m2-hosting.svc.cluster.local'
                and url.port in (None, 5432) and bool(url.database) and bool(url.password)
                and not url.query)
        urls[filename] = url
    require(urls['database-url'].database == urls['admin-database-url'].database)
    # Refuse overwrites, unknown query overrides and alternate DNS/service inputs.
    # Original source files are never touched.
    output.mkdir(mode=0o700)
    for filename, url in urls.items():
        fd = os.open(output/filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as target:
            target.write(url.set(host='127.0.0.1', port=port).render_as_string(hide_password=False))
            target.flush(); os.fsync(target.fileno())


def audit(url, owner):
    engine = create_engine(url, hide_parameters=True, connect_args={
        'connect_timeout': 3, 'options': '-c statement_timeout=5000 -c lock_timeout=3000'})
    try:
        with engine.begin() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            account = db.execute(text('SELECT role,is_approved FROM auth.accounts WHERE id=:owner'),
                                 {'owner': owner}).one_or_none()
            eligible = account is not None and account.role in {'admin', 'editor'} and account.is_approved is True
            present = db.execute(text("SELECT to_regnamespace('runner_runtime') IS NOT NULL")).scalar_one()
            row_present = False
            if present:
                validate(db)  # Partial/unknown migration is not reset or adopted.
                row_present = db.execute(text('SELECT EXISTS(SELECT 1 FROM runner_runtime.registry WHERE owner_id=:owner)'),
                                         {'owner': owner}).scalar_one()
            return {'runtimeSchemaPresent': bool(present), 'ownerRegistryPresent': bool(row_present),
                    'ownerEligible': bool(eligible), 'retryAllowed': bool(eligible and not row_present)}
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    staging = modes.add_parser('stage')
    staging.add_argument('--source-dir', required=True)
    staging.add_argument('--output-dir', required=True)
    staging.add_argument('--port', type=int, default=25413)
    checking = modes.add_parser('audit')
    checking.add_argument('--database-url-file', required=True)
    checking.add_argument('--inputs', required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'stage':
            stage(Path(args.source_dir), Path(args.output_dir), args.port)
            print('PASS private loopback DB copies created; original inputs unchanged')
            return 0
        owner = UUID(read_private(Path(args.inputs))['owner'])
        url = read_private(Path(args.database_url_file), json_format=False).strip()
        result = audit(url, owner)
        print(json.dumps(result))
        return 0 if result['retryAllowed'] else 1
    except Exception:
        print('DB route/audit refused; preserve prior output. Private values are not printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
