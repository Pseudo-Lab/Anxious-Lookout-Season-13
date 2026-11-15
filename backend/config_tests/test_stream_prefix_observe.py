"""#94 (8): the prefix outcome is observable. `remember` logs `runner_stream_prefix` (result, and whether the literals
being installed - the current credential - are covered), and the policy splits L_legacy into the current credential's
part and the retired literals' part (runner_stream_summary legacyCurrentL / legacyRetiredL). Pure: no runner."""
import json
import logging

import pytest

from runner import stream_prefix
from runner.stream import Turn
from runner.stream_prefix import policy
from test_stream_prefix import JWT_HEAD, control_dir, journal_with

OLD, NEW = JWT_HEAD + 'o' * 1600, JWT_HEAD + 'n' * 1639  # an earlier and the current access token (lengths ≈ #93)
EMAIL = 'someone.long.address@example.invalid'


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    def read():
        return [json.loads(r.getMessage().partition(' ')[2]) for r in caplog.records
                if r.getMessage().startswith('runner_stream_prefix ')]
    return read


def no_plaintext(caplog, journal, *values):
    for value in values:
        assert value[:stream_prefix.P] not in caplog.text and journal.digest(value) not in caplog.text


def test_a_written_install_logs_the_current_literals_covered(tmp_path, lines, caplog):
    journal = journal_with([NEW, EMAIL])
    control = control_dir(tmp_path)
    assert stream_prefix.remember(control, journal, [NEW, EMAIL, 'acct']) is True
    assert lines() == [{'result': 'written', 'literals': 2, 'covered': 2, 'currentCovered': True, 'entries': 2,
                        'limit': stream_prefix.LIMIT}]
    assert stream_prefix.remember(control, journal, [NEW, EMAIL]) is False
    assert lines()[-1]['result'] == 'unchanged' and lines()[-1]['currentCovered'] is True
    no_plaintext(caplog, journal, NEW, EMAIL)


def test_review_counterexample_a_failed_write_after_an_old_cover_is_visible(tmp_path, lines, monkeypatch, caplog):
    """review r0 major: present + covered >= 1 does not prove the current credential is covered."""
    journal = journal_with([OLD])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [OLD])  # the earlier credential got its fingerprint
    journal.observe([NEW])  # the new credential reaches the journal ...
    monkeypatch.setattr(stream_prefix, '_write', lambda path, payload: (_ for _ in ()).throw(OSError(28, 'full')))
    assert stream_prefix.remember(control, journal, [NEW]) is False  # ... but its fingerprint write fails
    assert lines()[-1] == {'result': 'failed', 'literals': 1, 'covered': 0, 'currentCovered': False, 'entries': 1,
                           'limit': stream_prefix.LIMIT}
    hold = policy(journal, control, [NEW])
    assert hold.covered and hold.legacy == len(NEW)  # the aggregate view: present, covered >= 1, legacy L
    assert (hold.legacy_current, hold.legacy_retired) == (len(NEW), 0)  # the split names the current literal
    no_plaintext(caplog, journal, OLD, NEW)


def test_a_full_file_is_visible_and_the_current_literal_counts_as_current(tmp_path, lines, monkeypatch):
    journal = journal_with([OLD])
    control = control_dir(tmp_path)
    monkeypatch.setattr(stream_prefix, 'LIMIT', 1)
    stream_prefix.remember(control, journal, [OLD])
    journal.observe([NEW])
    assert stream_prefix.remember(control, journal, [NEW]) is False
    assert lines()[-1] == {'result': 'full', 'literals': 1, 'covered': 0, 'currentCovered': False, 'entries': 1,
                           'limit': 1}
    hold = policy(journal, control, [NEW])
    assert (hold.legacy_current, hold.legacy_retired) == (len(NEW), 0)


def test_case_b_only_a_retired_literal_is_legacy(tmp_path, lines):
    journal = journal_with([OLD])  # recorded before the fingerprint code existed: never remembered
    control = control_dir(tmp_path)
    journal.observe([NEW])
    stream_prefix.remember(control, journal, [NEW])
    assert lines()[-1]['currentCovered'] is True
    hold = policy(journal, control, [NEW])
    assert hold.legacy == len(OLD) and (hold.legacy_current, hold.legacy_retired) == (0, len(OLD))


def test_case_a_without_any_file_the_current_literal_is_legacy(tmp_path):
    journal = journal_with([OLD, NEW])
    control = control_dir(tmp_path)
    hold = policy(journal, control, [NEW])
    assert (hold.legacy, hold.legacy_current, hold.legacy_retired) == (len(NEW), len(NEW), len(OLD))


def test_the_split_does_not_change_the_holdback(tmp_path):
    journal = journal_with([OLD])
    control = control_dir(tmp_path)
    journal.observe([NEW])
    stream_prefix.remember(control, journal, [NEW])
    hold = policy(journal, control, [NEW])
    plain = stream_prefix.HoldPolicy(hold.short, hold.covered, hold.legacy, hold.groups, hold.filters, hold.key,
                                     hold.state)
    assert plain == hold and all(plain.start('x' * n) == hold.start('x' * n) for n in (0, 10, 2000, 4000))


def test_no_control_is_logged_and_the_logger_never_raises(lines):
    assert stream_prefix.remember(None, None, [NEW]) is False
    assert lines()[-1] == {'result': 'no_control', 'literals': 0, 'covered': None, 'currentCovered': None,
                           'entries': None, 'limit': stream_prefix.LIMIT}
    stream_prefix.observe('odd', object(), None, None)  # not serializable: swallowed


def test_a_turn_summary_starts_with_the_split_at_zero():
    stats = Turn('session', 'request').stats
    assert (stats['legacyL'], stats['legacyCurrentL'], stats['legacyRetiredL']) == (0, 0, 0)
