"""Frozen structural and application-grant checks for research readiness."""
import json
from pathlib import Path

from sqlalchemy import inspect, text

from .schema_contract import normalize


def table_contract(connection, name):
    return table_contracts(connection, [name])[name]


def table_contracts(connection, names):
    inspector = inspect(connection)
    options = {"schema": "research", "filter_names": list(names)}
    columns = inspector.get_multi_columns(**options)
    primary = inspector.get_multi_pk_constraint(**options)
    checks = inspector.get_multi_check_constraints(**options)
    foreign = inspector.get_multi_foreign_keys(**options)
    unique = inspector.get_multi_unique_constraints(**options)
    indexes = inspector.get_multi_indexes(**options)
    return {name: {
        "columns": {column["name"]: [str(column["type"].compile(dialect=connection.dialect)), column["nullable"], column["default"]]
                    for column in columns[("research", name)]},
        "primary": primary[("research", name)]["constrained_columns"],
        "checks": {value["name"]: value["sqltext"] for value in checks[("research", name)]},
        "foreign": [{key: value[key] for key in ("constrained_columns", "referred_schema", "referred_table", "referred_columns")}
                    for value in foreign[("research", name)]],
        "unique": {value["name"]: value["column_names"] for value in unique[("research", name)]},
        "indexes": {value["name"]: {"columns": value["column_names"], "unique": value["unique"],
                    "where": value.get("dialect_options", {}).get("postgresql_where")}
                    for value in indexes[("research", name)]},
    } for name in names}


def canonical(contract):
    result = dict(contract)
    result["columns"] = {name: [kind, nullable, normalize(default) if default else None]
                         for name, (kind, nullable, default) in contract["columns"].items()}
    result["checks"] = {name: normalize(value) for name, value in contract["checks"].items()}
    result["foreign"] = sorted(contract["foreign"], key=lambda value: json.dumps(value, sort_keys=True))
    result["indexes"] = {name: {**value, "where": normalize(value["where"]) if value["where"] else None}
                         for name, value in contract["indexes"].items()}
    return result


def validate_research(connection, check_privileges=True):
    tables = set(inspect(connection).get_table_names(schema="research"))
    expected = json.loads((Path(__file__).resolve().parent.parent / "db" / "002_research_contract.json").read_text())
    if not {"alembic_version", *expected}.issubset(tables):
        raise RuntimeError("Research schema is incomplete")
    marker = connection.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one()
    if marker not in {"0002_research", "0003_sessions"}:
        raise RuntimeError("Research revision is unsupported")
    if marker == "0003_sessions":
        expected.update(json.loads((Path(__file__).resolve().parent.parent / "db" / "003_sessions_contract.json").read_text()))
        if not set(expected).issubset(tables):
            raise RuntimeError("Conversation schema is incomplete")
    actual = table_contracts(connection, expected)
    for name, contract in expected.items():
        if canonical(actual[name]) != canonical(contract):
            raise RuntimeError("Research structure does not match reviewed migration")
    if connection.execute(text("""
        SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='research' AND (NOT c.convalidated OR c.condeferrable)
    """)).scalar_one():
        raise RuntimeError("Research constraints must be validated and immediate")
    if connection.execute(text("""
        SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='research' AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate)
    """)).scalar_one():
        raise RuntimeError("Research indexes must be valid and immediate")
    if not check_privileges:
        return marker  # Explicit isolated restore operator only; runtime callers always check grants.
    if not connection.execute(text("SELECT has_schema_privilege('anxious_api','research','USAGE')")).scalar_one():
        raise RuntimeError("Research schema grant is missing")
    required = {"items": {"SELECT", "INSERT", "UPDATE"}, "relations": {"SELECT", "INSERT", "UPDATE"},
                "versions": {"SELECT", "INSERT"}, "idempotency": {"SELECT", "INSERT"}, "alembic_version": {"SELECT"}}
    if marker == "0003_sessions":
        if "conversations" not in tables:
            raise RuntimeError("Conversation schema is incomplete")
        required["conversations"] = {"SELECT", "INSERT", "UPDATE"}
    # Check effective table AND column privileges (including inherited grants).
    # Batch the matrix rather than issuing a separate query for every cell.
    privileges = connection.execute(text("""
        SELECT c.relname, p.permission,
               has_table_privilege('anxious_api',c.oid,p.permission) AS table_granted,
               CASE WHEN p.permission IN ('SELECT','INSERT','UPDATE')
                    THEN has_any_column_privilege('anxious_api',c.oid,p.permission)
                    ELSE false END AS column_granted
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE')) p(permission)
        WHERE n.nspname='research' AND c.relname = ANY(:tables)
    """), {"tables": list(required)}).all()
    if len(privileges) != 5 * len(required):
        raise RuntimeError("Research privilege matrix is incomplete")
    for row in privileges:
        allowed = row.permission in required[row.relname]
        if row.table_granted != allowed or (not allowed and row.column_granted):
            raise RuntimeError("Research application privileges do not match reviewed grants")
    return marker
