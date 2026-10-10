"""Readonly account selection refuses ambiguous/ineligible identities before reuse."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
import owner_lookup


@pytest.mark.parametrize('rows,accepted', [
    ([], False),
    ([{'id': uuid4(), 'role': 'editor', 'is_approved': True}], True),
    ([{'id': uuid4(), 'role': 'admin', 'is_approved': True}], True),
    ([{'id': uuid4(), 'role': 'commenter', 'is_approved': True}], False),
    ([{'id': uuid4(), 'role': 'editor', 'is_approved': False}], False),
    ([{'id': uuid4(), 'role': 'editor', 'is_approved': True}] * 2, False),
])
def test_readonly_parameterized_existing_account_only(monkeypatch, rows, accepted):
    calls, disposed = [], []
    class Connection:
        def begin(self): return nullcontext()
        def execute(self, statement, parameters=None):
            calls.append((str(statement), parameters))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
    engine = SimpleNamespace(connect=lambda: nullcontext(Connection()), dispose=lambda: disposed.append(True))
    monkeypatch.setattr(owner_lookup.Settings, 'load', lambda: SimpleNamespace(database_url='synthetic'))
    monkeypatch.setattr(owner_lookup, 'create_engine', lambda url, **kw: engine)
    if accepted:
        result = owner_lookup.lookup({'githubLogin': 'Selected-User'})
        assert result['ownerId'] == str(rows[0]['id']) and result['readOnly'] is True
    else:
        with pytest.raises(ValueError): owner_lookup.lookup({'githubLogin': 'Selected-User'})
    assert calls[0] == ('SET TRANSACTION READ ONLY', None)
    assert calls[-1][1] == {'login': 'Selected-User'}
    assert calls[-1][0].startswith('SELECT ') and ':login' in calls[-1][0]
    assert not any('UPDATE ' in query or 'INSERT ' in query for query, _ in calls)
    assert disposed == [True]


@pytest.mark.parametrize('selection', [{}, {'githubLogin': 'x', 'ownerId': str(uuid4())}, {'githubLogin': "x' OR 1=1"}, {'githubLogin': None}])
def test_invalid_selection_never_opens_database(monkeypatch, selection):
    monkeypatch.setattr(owner_lookup, 'create_engine', lambda *a, **kw: pytest.fail('invalid selection opened DB'))
    with pytest.raises(ValueError): owner_lookup.lookup(selection)
