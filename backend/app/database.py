from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def database(url):
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=0, pool_timeout=3, hide_parameters=True, connect_args={"connect_timeout": 3})
    return engine, sessionmaker(engine, expire_on_commit=False)


def check_ready(engine):
    with engine.connect() as conn:
        # Verify connectivity AND the exact migration contract as the application role.
        if conn.execute(text("SELECT version FROM auth.schema_version WHERE singleton = true")).scalar_one() != 1:
            raise RuntimeError("Unsupported authentication schema")
        conn.execute(text("SELECT id FROM auth.accounts LIMIT 0"))
