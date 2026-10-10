"""PM readonly existing-account lookup; stdin selection/output must stay private."""
import json
import re
import sys

from sqlalchemy import create_engine, text
from app.settings import Settings


def lookup(selection):
    if (not isinstance(selection, dict) or set(selection) != {"githubLogin"}
        or not isinstance(selection["githubLogin"], str)
        or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", selection["githubLogin"])):
        raise ValueError()
    engine = create_engine(Settings.load().database_url, hide_parameters=True)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            connection.execute(text("SET LOCAL lock_timeout='2s'"))
            rows = connection.execute(text("SELECT id, role, is_approved FROM auth.accounts WHERE lower(login)=lower(:login)"),
                                      {"login": selection["githubLogin"]}).mappings().all()
            if len(rows) != 1 or rows[0]["role"] not in {"editor", "admin"} or rows[0]["is_approved"] is not True:
                raise ValueError()
            return {"ownerId": str(rows[0]["id"]), "approved": True, "toolRoleEligible": True, "readOnly": True}
    finally:
        engine.dispose()


if __name__ == "__main__":
    try: print(json.dumps(lookup(json.load(sys.stdin))))
    except Exception:
        print("Existing owner lookup refused; no account or credential values printed", file=sys.stderr)
        sys.exit(1)
