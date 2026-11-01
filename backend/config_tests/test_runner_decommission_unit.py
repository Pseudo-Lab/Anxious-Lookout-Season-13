"""#66 decommission helpers without a database."""
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from ops import runner_decommission as tool
from runner.lifecycle import Refused


def test_fence_requires_exact_fields_and_integer_counters():
    owner, state = uuid4(), uuid4()
    value = {'owner': str(owner), 'state': str(state), 'generation': 9, 'revision': 38}
    assert tool.fence_of(value) == {'owner': owner, 'state': state, 'generation': 9, 'revision': 38}
    for broken in ({**value, 'generation': '9'}, {**value, 'revision': 38.0}, {**value, 'extra': 1},
                   {k: v for k, v in value.items() if k != 'state'}):
        with pytest.raises(Refused):
            tool.fence_of(broken)
    with pytest.raises(ValueError):
        tool.fence_of({**value, 'owner': 'not-a-uuid'})


def test_digest_is_stable_and_covers_hidden_columns():
    when = datetime(2026, 10, 26, 3, 47, tzinfo=timezone.utc)
    row = {'owner_id': uuid4(), 'session_hash': 'a'*64, 'created_at': when, 'receipt': {'b': 1, 'a': [uuid4()]}}
    first = tool.digest([row])
    assert first == tool.digest([dict(reversed(list(row.items())))])
    assert tool.digest([{**row, 'session_hash': 'b'*64}]) != first
    assert 'a'*64 not in first and len(first) == 64


def test_refusal_carries_code_and_observed_values_only():
    with pytest.raises(tool.Refusal) as caught:
        tool.check(False, 'not_idle_stopped', phase='runner_unreachable', podUid=None)
    assert caught.value.code == 'not_idle_stopped'
    assert caught.value.observed == {'phase': 'runner_unreachable', 'podUid': None}
    tool.check(True, 'unused')


def test_tables_delete_children_before_registry():
    names = [table for table, _ in tool.TABLES]
    assert names[-1] == 'runner_runtime.registry' and len(set(names)) == len(names) == 8
    assert names.index('runner_bindings.resources') < names.index('runner_bindings.bindings')
    assert set(tool.BY_STATE) < set(names)


def test_refusal_is_printed_as_json_with_rc3(monkeypatch, capsys, tmp_path):
    inputs = tmp_path/'inputs.json'
    monkeypatch.setattr('sys.argv', ['runner_decommission', 'report', '--inputs', str(inputs),
                                     '--export', str(tmp_path/'export.json')])
    monkeypatch.setattr('os.geteuid', lambda: 0)
    owner = uuid4()
    monkeypatch.setattr('runner.auth.read_private', lambda path: {'owner': str(owner), 'state': str(uuid4()),
                                                                  'generation': 9, 'revision': 38})
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def rollback(self): self.rolled = True
    class Engine:
        def connect(self): return Connection()
        def dispose(self): pass
    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: Engine())
    monkeypatch.setattr('app.settings.secret', lambda name: 'postgresql://synthetic')
    def refuse(db, fence):
        raise tool.Refusal('not_idle_stopped', phase='runner_unreachable', podUid=uuid4())
    monkeypatch.setattr(tool, 'export', refuse)
    with pytest.raises(SystemExit) as caught:
        tool.main()
    assert caught.value.code == 3
    line = json.loads(capsys.readouterr().out)
    assert line['result'] == 'refused' and line['code'] == 'not_idle_stopped'
    assert line['observed']['phase'] == 'runner_unreachable'
    assert not (tmp_path/'export.json').exists()


def test_unexpected_error_exposes_no_text(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr('sys.argv', ['runner_decommission', 'apply', '--inputs', str(tmp_path/'missing.json'),
                                     '--export', str(tmp_path/'export.json')])
    monkeypatch.setattr('os.geteuid', lambda: 0)
    with pytest.raises(SystemExit) as caught:
        tool.main()
    captured = capsys.readouterr()
    assert caught.value.code == 1 and 'missing.json' not in captured.out + captured.err
    assert 'Decommission refused' in captured.err
