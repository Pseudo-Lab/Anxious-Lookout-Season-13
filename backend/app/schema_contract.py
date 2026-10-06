"""Frozen M2 v1 compatibility checks before adopting a pre-Alembic database."""
import re

from sqlalchemy import inspect, text

AUTH_REVISION = "0001_auth"
EXPECTED = {
    "schema_version": {"singleton": "BOOLEAN", "version": "INTEGER"},
    "accounts": {"id": "UUID", "github_id": "VARCHAR(32)", "login": "VARCHAR(100)", "role": "VARCHAR(16)", "is_approved": "BOOLEAN", "created_at": "TIMESTAMP WITH TIME ZONE", "updated_at": "TIMESTAMP WITH TIME ZONE"},
    "sessions": {"token_hash": "VARCHAR(64)", "account_id": "UUID", "csrf_token": "VARCHAR(100)", "expires_at": "TIMESTAMP WITH TIME ZONE"},
    "oauth_transactions": {"state_hash": "VARCHAR(64)", "binding_hash": "VARCHAR(64)", "verifier": "TEXT", "expires_at": "TIMESTAMP WITH TIME ZONE"},
    "permission_audit": {"id": "UUID", "actor": "VARCHAR(200)", "account_id": "UUID", "reason": "TEXT", "before": "JSONB", "after": "JSONB", "created_at": "TIMESTAMP WITH TIME ZONE"},
}
PRIMARY = {"schema_version": "singleton", "accounts": "id", "sessions": "token_hash", "oauth_transactions": "state_hash", "permission_audit": "id"}
DEFAULTS = {
    "schema_version": {"singleton": "true"},
    "accounts": {"id": "gen_random_uuid", "role": "'commenter'", "is_approved": "false", "created_at": "now", "updated_at": "now"},
    "permission_audit": {"id": "gen_random_uuid", "created_at": "now"},
}


def normalize(expression):
    expression = re.sub(r"::(?:character varying|text|boolean)(?:\[\])?", "", expression.lower())
    return re.sub(r"[\s()]", "", expression)


def validate_v1(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="auth"))
    if not set(EXPECTED).issubset(tables):
        raise RuntimeError("Existing authentication schema is incomplete")
    for table, expected in EXPECTED.items():
        columns = inspector.get_columns(table, schema="auth")
        actual = {column["name"]: str(column["type"].compile(dialect=connection.dialect)) for column in columns}
        if actual != expected or any(column["nullable"] for column in columns):
            raise RuntimeError("Existing authentication columns do not match v1")
        if inspector.get_pk_constraint(table, schema="auth")["constrained_columns"] != [PRIMARY[table]]:
            raise RuntimeError("Existing authentication primary key does not match v1")
        defaults = {column["name"]: normalize(column["default"]) for column in columns if column["default"] is not None}
        if defaults != DEFAULTS.get(table, {}):
            raise RuntimeError("Existing authentication defaults do not match v1")
    if connection.execute(text("SELECT version FROM auth.schema_version WHERE singleton")).scalar_one() != 1:
        raise RuntimeError("Existing authentication version is unsupported")
    checks = {normalize(c["sqltext"]) for c in inspector.get_check_constraints("accounts", schema="auth")}
    if not {"role=anyarray['commenter','editor','admin']", "github_id~'^[0-9]+$'"}.issubset(checks):
        raise RuntimeError("Existing account constraints do not match v1")
    singleton_checks = {normalize(c["sqltext"]) for c in inspector.get_check_constraints("schema_version", schema="auth")}
    if "singleton" not in singleton_checks:
        raise RuntimeError("Existing schema marker constraint does not match v1")
    if connection.execute(text("SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='auth' AND NOT c.convalidated")).scalar_one():
        raise RuntimeError("Existing authentication constraints are unvalidated")
    unique = inspector.get_unique_constraints("accounts", schema="auth")
    if not any(c["column_names"] == ["github_id"] for c in unique):
        raise RuntimeError("Existing provider identity uniqueness is missing")
    for table in ("sessions", "permission_audit"):
        foreign = inspector.get_foreign_keys(table, schema="auth")
        if not any(c["constrained_columns"] == ["account_id"] and c["referred_schema"] == "auth" and c["referred_table"] == "accounts" and c["referred_columns"] == ["id"] for c in foreign):
            raise RuntimeError("Existing authentication foreign key does not match v1")
