"""A request-local deadline for PostgreSQL waits (#114).

Inside `bounded(end)` every statement on an installed engine first sets `lock_timeout` and
`statement_timeout` to the time left, so a row or advisory lock held elsewhere cannot hold the caller
past its deadline (the pre-stop sync inside logout). Outside it nothing changes. The shared pool
timeout is never touched; a pool wait stays outside this budget.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import time

from sqlalchemy import event

DEADLINE = ContextVar('sql_deadline', default=None)


def install(engine):
    """Idempotent; the listener acts only while a deadline is set in this context."""
    if engine is None or getattr(engine, '_sql_budget_installed', False):
        return
    def bounded_sql(connection, cursor, statement, parameters, context, executemany):
        end = DEADLINE.get()
        if end is not None:
            milliseconds = max(1, int((end - time.monotonic()) * 1000))  # spent: fail at once
            cursor.execute('SET LOCAL lock_timeout = ' + str(milliseconds))
            cursor.execute('SET LOCAL statement_timeout = ' + str(milliseconds))
    event.listen(engine, 'before_cursor_execute', bounded_sql)
    engine._sql_budget_installed = True


@contextmanager
def bounded(end):
    """`end` on the time.monotonic clock."""
    marker = DEADLINE.set(end)
    try:
        yield
    finally:
        DEADLINE.reset(marker)
