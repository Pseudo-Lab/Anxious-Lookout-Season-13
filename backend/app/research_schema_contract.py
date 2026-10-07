"""Frozen structural and application-grant checks for research readiness."""
import json
from pathlib import Path

from sqlalchemy import inspect, text

from .schema_contract import normalize


def table_contract(connection, name):
    inspector = inspect(connection)
    return {
        "columns": {column["name"]: [str(column["type"].compile(dialect=connection.dialect)), column["nullable"], column["default"]]
                    for column in inspector.get_columns(name, schema="research")},
        "primary": inspector.get_pk_constraint(name, schema="research")["constrained_columns"],
        "checks": {value["name"]: value["sqltext"] for value in inspector.get_check_constraints(name, schema="research")},
        "foreign": [{key: value[key] for key in ("constrained_columns", "referred_schema", "referred_table", "referred_columns")}
                    for value in inspector.get_foreign_keys(name, schema="research")],
        "unique": {value["name"]: value["column_names"] for value in inspector.get_unique_constraints(name, schema="research")},
        "indexes": {value["name"]: {"columns": value["column_names"], "unique": value["unique"],
                    "where": value.get("dialect_options", {}).get("postgresql_where")}
                    for value in inspector.get_indexes(name, schema="research")},
    }


def canonical(contract):
    result = dict(contract)
    result["columns"] = {name: [kind, nullable, normalize(default) if default else None]
                         for name, (kind, nullable, default) in contract["columns"].items()}
    result["checks"] = {name: normalize(value) for name, value in contract["checks"].items()}
    result["foreign"] = sorted(contract["foreign"], key=lambda value: json.dumps(value, sort_keys=True))
    result["indexes"] = {name: {**value, "where": normalize(value["where"]) if value["where"] else None}
                         for name, value in contract["indexes"].items()}
    return result


def validate_research(connection):
    tables = set(inspect(connection).get_table_names(schema="research"))
    expected = json.loads((Path(__file__).resolve().parent.parent / "db" / "002_research_contract.json").read_text())
    if not {"alembic_version", *expected}.issubset(tables):
        raise RuntimeError("Research schema is incomplete")
    marker = connection.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one()
    if marker != "0002_research":
        raise RuntimeError("Research revision is unsupported")
    for name, contract in expected.items():
        if canonical(table_contract(connection, name)) != canonical(contract):
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
    if not connection.execute(text("SELECT has_schema_privilege('anxious_api','research','USAGE')")).scalar_one():
        raise RuntimeError("Research schema grant is missing")
    required = {"items": {"SELECT", "INSERT", "UPDATE"}, "relations": {"SELECT", "INSERT", "UPDATE"},
                "versions": {"SELECT", "INSERT"}, "idempotency": {"SELECT", "INSERT"}, "alembic_version": {"SELECT"}}
    for name, permissions in required.items():
        for permission in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            granted = connection.execute(text("SELECT has_table_privilege('anxious_api',:name,:permission)"),
                        {"name": "research." + name, "permission": permission}).scalar_one()
            if granted != (permission in permissions):
                raise RuntimeError("Research application privileges do not match reviewed grants")
    return marker
