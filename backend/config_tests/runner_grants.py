"""Install a reviewed runner grant file into the current test database, every time.

Roles are cluster-wide and outlive a dropped schema (or another database of the same cluster), while
table grants do not. Skipping a grant file because its role already exists left later suites without
their grants (#101 review); only the file's CREATE ROLE of an existing role is left out here."""
from pathlib import Path
import re

from sqlalchemy import text

DB = Path(__file__).resolve().parent.parent/'db'


def install_grant_file(db, name):
    sql = (DB/name).read_text()
    for role in re.findall(r'^CREATE ROLE (\w+) NOLOGIN;$', sql, re.M):
        if db.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).first():
            sql = re.sub(r'^CREATE ROLE '+role+r' NOLOGIN;$', '', sql, flags=re.M)
    db.connection.driver_connection.execute(sql)
