from alembic import context

connection = context.config.attributes.get("connection")
if connection is None or context.is_offline_mode():
    raise RuntimeError("Use the explicit app.migrate Job connection")
context.configure(connection=connection, version_table_schema="research", transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
