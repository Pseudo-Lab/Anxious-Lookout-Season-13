"""Request-local authorization/SQL budgets; never mutate the shared pool timeout."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time

from sqlalchemy import event

from .research import fail
from . import runner_http_budget

CURRENT = ContextVar('research_api_deadline', default=None)


@dataclass
class Deadline:
    end: float

    def remaining(self):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            fail('research_authorization_stale', 'Research authorization timed out', 503)
        return remaining


def checkout():
    current = CURRENT.get()
    # The existing pool timeout is3s. Refuse early when that entire wait cannot
    # fit; changing engine.pool._timeout would race unrelated requests.
    if current is not None and current.remaining() < 3:
        fail('research_authorization_stale', 'Research authorization timed out', 503)


def install(engine):
    if getattr(engine, '_research_api_budget_installed', False):
        return
    def bounded_sql(connection, cursor, statement, parameters, context, executemany):
        current = CURRENT.get()
        if current is not None:
            milliseconds = max(1, int(min(3, current.remaining()) * 1000))
            cursor.execute('SET LOCAL statement_timeout = ' + str(milliseconds))
            cursor.execute('SET LOCAL lock_timeout = ' + str(min(1000, milliseconds)))
    event.listen(engine, 'before_cursor_execute', bounded_sql)
    engine._research_api_budget_installed = True


@contextmanager
def authorization():
    current = Deadline(time.monotonic() + 15)
    marker = CURRENT.set(current)
    network = runner_http_budget.begin(current.end, time.monotonic)
    try:
        yield current
    finally:
        runner_http_budget.finish(network)
        CURRENT.reset(marker)
