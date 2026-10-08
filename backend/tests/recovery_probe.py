"""Docker-only fixture comparison for a full-dump isolated restore candidate."""
import hashlib
import json
import os
import sys

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.database import check_ready
from app.research_schema_contract import validate_research
from app.restore_research import restore_candidate

TABLES = ("auth.accounts", "auth.permission_audit", "research.items", "research.versions",
          "research.relations", "research.idempotency", "research.conversations")


def signature(connection, revoked_grants=False):
    tables = TABLES
    marker = connection.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one()
    if marker == "0004_publication":
        tables += ("research.publications", "research.publication_heads")
    def rows(name):
        values = [dict(row._mapping) for row in connection.execute(text("SELECT * FROM " + name))]
        if name == "research.conversations" and revoked_grants:
            for row in values:
                for field in ("tool_token_hash", "tool_expires_at", "login_hash"):
                    row.pop(field)
        return sorted(values, key=lambda row: json.dumps(row, default=str, sort_keys=True))
    return {name: hashlib.sha256(json.dumps(rows(name), default=str, sort_keys=True).encode()).hexdigest()
            for name in tables}


def run(mode):
    source = create_engine(os.environ["ADMIN_DATABASE_URL"], hide_parameters=True)
    if mode == "m2":
        origin = os.environ["AUTH_ORIGIN"]
        with httpx.Client(trust_env=False, timeout=5) as client:
            result = client.get("http://m2-api:8080/readyz", headers={"Host": origin.removeprefix("http://")})
            assert result.status_code == 200
            version = client.get("http://m2-api:8080/api/version").json()
            assert version["sha"] == os.environ["M2_BASELINE_SHA"]
        with source.connect() as db:
            assert validate_research(db) in {"0003_sessions", "0004_publication"}
            assert db.execute(text("SELECT count(*) FROM research.versions")).scalar_one() >= 3
            assert db.execute(text("SELECT count(*) FROM research.relations")).scalar_one() >= 1
            assert db.execute(text("SELECT count(*) FROM research.conversations")).scalar_one() >= 1
        print("PASS original M2 image readiness/version on populated M3 DB; research records retained")
    elif mode == "restore":
        target_name = os.environ.get("RESTORE_FIXTURE_DATABASE", "restore_m3_fixture")
        candidate_url = make_url(os.environ["ADMIN_DATABASE_URL"]).set(database=target_name)
        target = create_engine(candidate_url, hide_parameters=True)
        with source.connect() as original, target.connect() as restored:
            assert signature(original) == signature(restored)
            marker = validate_research(restored, check_privileges=False)
            assert marker == validate_research(original)
        with target.connect() as restored:
            assert restored.execute(text("SELECT has_table_privilege('anxious_api','research.versions','INSERT')")).scalar_one() is False
        assert restore_candidate(candidate_url, os.environ["API_DATABASE_PASSWORD"], target_name) == marker
        api_url = make_url(os.environ["DATABASE_URL"]).set(database=target_name)
        api = create_engine(api_url, hide_parameters=True)
        with api.connect() as db:
            validate_research(db)
            assert db.execute(text("SELECT count(*) FROM auth.sessions")).scalar_one() == 0
            assert db.execute(text("SELECT count(*) FROM auth.oauth_transactions")).scalar_one() == 0
            assert db.execute(text("SELECT count(*) FROM research.conversations WHERE tool_token_hash IS NOT NULL")).scalar_one() == 0
        check_ready(api)
        with source.connect() as original, target.connect() as restored:
            assert original.execute(text("SELECT count(*) FROM research.conversations WHERE state='running'")).scalar_one() == 0
            # Idle cache/context/content remain exact. Even idle native sessions
            # may retain inactive token hashes; recovery deliberately revokes them.
            assert signature(original, revoked_grants=True) == signature(restored, revoked_grants=True)
        api.dispose()
        target.dispose()
        print("PASS full pg_dump/pg_restore: auth/research content/version/relation/idempotency/session records match; reviewed grants and M2 readiness restored; stale auth/tool sessions revoked")
    else:
        raise RuntimeError("Unknown isolated recovery fixture mode")
    source.dispose()


if __name__ == "__main__":
    run(sys.argv[1])
