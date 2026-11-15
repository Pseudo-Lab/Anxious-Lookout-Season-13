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


@pytest.fixture(autouse=True)
def fresh_observations(monkeypatch):
    """The dedup state is per process; each test starts from none."""
    monkeypatch.setattr(stream_prefix, '_observed', {})


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
    assert lines() == [{'result': 'written', 'literals': 2, 'covered': 2, 'allCovered': True, 'entries': 2,
                        'limit': stream_prefix.LIMIT}]
    assert stream_prefix.remember(control, journal, [NEW, EMAIL]) is False
    assert lines()[-1]['result'] == 'unchanged' and lines()[-1]['allCovered'] is True
    no_plaintext(caplog, journal, NEW, EMAIL)


def test_review_counterexample_a_failed_write_after_an_old_cover_is_visible(tmp_path, lines, monkeypatch, caplog):
    """review r0 major: present + covered >= 1 does not prove the current credential is covered."""
    journal = journal_with([OLD])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [OLD])  # the earlier credential got its fingerprint
    journal.observe([NEW])  # the new credential reaches the journal ...
    monkeypatch.setattr(stream_prefix, '_write', lambda path, payload: (_ for _ in ()).throw(OSError(28, 'full')))
    assert stream_prefix.remember(control, journal, [NEW]) is False  # ... but its fingerprint write fails
    assert lines()[-1] == {'result': 'failed', 'literals': 1, 'covered': 0, 'allCovered': False, 'entries': 1,
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
    assert lines()[-1] == {'result': 'full', 'literals': 1, 'covered': 0, 'allCovered': False, 'entries': 1,
                           'limit': 1}
    hold = policy(journal, control, [NEW])
    assert (hold.legacy_current, hold.legacy_retired) == (len(NEW), 0)


def test_case_b_only_a_retired_literal_is_legacy(tmp_path, lines):
    journal = journal_with([OLD])  # recorded before the fingerprint code existed: never remembered
    control = control_dir(tmp_path)
    journal.observe([NEW])
    stream_prefix.remember(control, journal, [NEW])
    assert lines()[-1]['allCovered'] is True
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
    assert lines()[-1] == {'result': 'no_control', 'literals': 0, 'covered': None, 'allCovered': None,
                           'entries': None, 'limit': stream_prefix.LIMIT}
    stream_prefix.observe('control', 'odd', object(), None, None)  # not serializable: swallowed


def test_a_turn_summary_starts_with_the_split_at_zero():
    stats = Turn('session', 'request').stats
    assert (stats['legacyL'], stats['legacyCurrentL'], stats['legacyRetiredL']) == (0, 0, 0)


# --- review r1 majors (2026-11-15) ---------------------------------------------------------------------------------

class FakeAuth:
    """What Sanitizer.generation reads: `secrets` (cumulative), `latest_secrets`, `journal`, `control`. Its refresh is
    a no-op: the file state is set by the test. (A real auth's refresh remembers the whole cumulative `secrets`, so in
    one process a rotated-out token is normally covered too; the state below - the new token covered, the old one not
    - arises when the old token's write failed or it was recorded before r4.)"""

    def __init__(self, journal, control, secrets, latest):
        self.journal, self.control, self.secrets, self.latest_secrets = journal, control, set(secrets), latest

    def refresh_projection_secrets(self):
        pass


def test_a_rotated_out_token_is_retired_not_current_through_the_real_path(tmp_path):
    """review r1 major 1: `secrets` keeps a token rotated out in this process; the split must name it retired."""
    from runner.auth import PersonalAuth
    from runner.stream import Sanitizer
    auth = object.__new__(PersonalAuth)
    auth.secrets, auth.latest_secrets = set(), set()
    auth.grant = lambda: {'providerAccountId': 'acct-000000000000000000000'}

    def cache(token):
        return {'tokens': dict(id_token=token, access_token=token, refresh_token=token,
                               account_id='acct-000000000000000000000')}
    auth.validate_cache(cache(OLD))
    auth.validate_cache(cache(NEW))
    assert OLD in auth.secrets and auth.latest_secrets == {NEW, 'acct-000000000000000000000'}
    journal = journal_with(sorted(auth.secrets))
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, sorted(auth.latest_secrets))  # only the new credential is covered
    hold = policy(journal, control, sorted(auth.secrets), sorted(auth.latest_secrets))
    assert (hold.legacy, hold.legacy_current, hold.legacy_retired) == (len(OLD), 0, len(OLD))
    _, _, through = Sanitizer(FakeAuth(journal, control, auth.secrets, auth.latest_secrets)).generation()
    assert (through.legacy, through.legacy_current, through.legacy_retired) == (len(OLD), 0, len(OLD))


@pytest.mark.parametrize('latest', [None, [], set()])
def test_no_or_empty_latest_counts_current_as_current_never_as_retired(tmp_path, latest):
    journal = journal_with([OLD, NEW])
    control = control_dir(tmp_path)
    hold = policy(journal, control, [OLD, NEW], latest)
    assert hold.legacy_current == len(NEW) and hold.legacy == len(NEW)
    from runner.stream import Sanitizer
    _, _, through = Sanitizer(FakeAuth(journal, control, [OLD, NEW], latest)).generation()
    assert through.legacy_current == max(len(OLD), len(NEW))  # the split errs to "current" (no §6 B case)
    assert through.legacy_retired == 0


def test_the_holdback_does_not_depend_on_latest(tmp_path):
    journal = journal_with([OLD, NEW])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [NEW])
    one, two = policy(journal, control, [OLD, NEW]), policy(journal, control, [OLD, NEW], [NEW])
    assert one == two and all(one.start('x' * n) == two.start('x' * n) for n in (0, 100, 1700, 4000))


def test_the_same_state_100_times_logs_one_line_and_reads_once_per_call(tmp_path, lines, monkeypatch):
    """review r1 major 2 / PM: lines and reads do not grow with deltas or sanitize calls."""
    journal = journal_with([NEW])
    control = control_dir(tmp_path)
    reads = []
    real = stream_prefix._read
    monkeypatch.setattr(stream_prefix, '_read', lambda path: reads.append(1) or real(path))
    for _ in range(100):
        stream_prefix.remember(control, journal, [NEW])
    assert [line['result'] for line in lines()] == ['written', 'unchanged']  # the first write, then one steady state
    assert len(reads) == 100  # the existing per-call verification only; no read-back added (was 200 in r1)


def test_a_failure_its_repeats_and_its_recovery(tmp_path, lines, monkeypatch):
    journal = journal_with([NEW])
    control = control_dir(tmp_path)
    broken = lambda path, payload: (_ for _ in ()).throw(OSError(28, 'full'))
    real = stream_prefix._write
    monkeypatch.setattr(stream_prefix, '_write', broken)
    for _ in range(50):
        stream_prefix.remember(control, journal, [NEW])
    monkeypatch.setattr(stream_prefix, '_write', real)
    for _ in range(50):
        stream_prefix.remember(control, journal, [NEW])
    assert [(line['result'], line['allCovered']) for line in lines()] == [
        ('failed', False), ('written', True), ('unchanged', True)]


def test_the_dedup_state_is_bounded(tmp_path, lines, monkeypatch):
    monkeypatch.setattr(stream_prefix, 'OBSERVED_LIMIT', 3)
    journal = journal_with([NEW])
    for i in range(5):
        (tmp_path / str(i)).mkdir()
    folders = [control_dir(tmp_path / str(i)) for i in range(5)]
    for folder in folders:
        stream_prefix.remember(folder, journal, [NEW])
    assert len(stream_prefix._observed) <= 3
    assert len(lines()) == 5  # each directory's first state is logged once


def test_review_r2_a_full_aggregate_with_a_covered_latest_is_retired_only(tmp_path, lines, monkeypatch):
    """review r2 major: the prefix line covers the caller's (cumulative) set; the latest credential's state is the
    summary split. Capacity 1, only the new token stored, journal old + new, next call with old + new: the line says
    full / allCovered false, and the split says current 0, retired len(OLD) - the post-rollout check reads (b) here,
    not (a)."""
    journal = journal_with([OLD, NEW])
    control = control_dir(tmp_path)
    monkeypatch.setattr(stream_prefix, 'LIMIT', 1)
    stream_prefix.remember(control, journal, [NEW])
    stream_prefix.remember(control, journal, [OLD, NEW])
    assert (lines()[-1]['result'], lines()[-1]['allCovered']) == ('full', False)
    hold = policy(journal, control, [OLD, NEW], [NEW])
    assert (hold.legacy_current, hold.legacy_retired) == (0, len(OLD))
    assert 'currentCovered' not in lines()[-1]
