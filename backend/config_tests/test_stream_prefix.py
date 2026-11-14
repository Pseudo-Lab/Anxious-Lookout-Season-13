"""#90 design r4 / r4.1: the stream holdback's prefix fingerprints (runner/stream_prefix.py) with the r3 left-only
cut (runner/stream.py Holdback.plan). Pure: no runner, no network."""
import json
import os
import random
import string

import pytest

from runner import stream_prefix
from runner.projection import ProjectionJournal
from runner.stream import Holdback, MARKER
from runner.stream_prefix import P, HoldPolicy, policy

OWNER, TRIAL = "00000000-0000-4000-8000-000000005805", "11111111-2222-4333-8444-555555555555"
JWT_HEAD = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."  # a header many literals share (longer than P)


def journal_with(literals):
    journal = ProjectionJournal.create(OWNER, TRIAL)
    journal.observe(literals)
    return journal


def control_dir(tmp_path):
    folder = tmp_path / "control"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def stream(text, journal, hold_of, chunks):
    """Feed `text` in `chunks`, plan after each with the policy of that moment; return the joined emission and the
    largest unemitted length seen after a non-final plan whose window had no span crossing its cut."""
    holdback, emitted, position, peaks = Holdback(), [], 0, []
    for size in chunks:
        holdback.feed(text[position:position + size])
        position += size
        window = holdback.raw
        out, consumed, joined = Holdback.plan(window, journal.spans(window), hold_of(), False, holdback.joined)
        holdback.commit(consumed, joined, len(window))
        emitted.append(out)
        peaks.append(len(holdback.raw))
    out, consumed, joined = Holdback.plan(holdback.raw, journal.spans(holdback.raw), hold_of(), True, holdback.joined)
    holdback.commit(consumed, joined, len(holdback.raw))
    emitted.append(out)
    return "".join(emitted), peaks


def splits(n, rng):
    yield [1] * n
    for _ in range(4):
        cuts, left = [], n
        while left:
            size = rng.randint(1, min(left, 40))
            cuts.append(size)
            left -= size
        yield cuts


def random_literal(rng, length, head=""):
    alphabet = string.ascii_letters + string.digits + "-_"
    return (head + "".join(rng.choice(alphabet) for _ in range(length)))[:length] if length > len(head) else \
        "".join(rng.choice(alphabet) for _ in range(length))


# --- oracle (design r3 + r4 §7) ------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(12))
def test_oracle_any_split_equals_the_whole_text_sanitized(tmp_path, seed):
    rng = random.Random(seed)
    lengths = [1, P, P + 1, 26, 36, 43, 120, 1676][: 3 + seed % 6]
    literals = [random_literal(rng, n, JWT_HEAD if n > 40 and rng.random() < 0.6 else "") for n in lengths]
    covered = [v for v in literals if len(v) > P and rng.random() < 0.6]  # the rest of the long ones stay legacy
    journal = journal_with(literals)
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, covered)
    hold = policy(journal, control)
    assert hold.legacy == max((len(v) for v in literals if len(v) > P and v not in covered), default=0)
    pieces = []
    for _ in range(6):
        pieces.append("".join(rng.choice(string.ascii_lowercase + " ") for _ in range(rng.randint(0, 30))))
        value = rng.choice(literals)
        if rng.random() < 0.3:  # a partial or overlapping occurrence
            value = value[: rng.randint(1, len(value))] + rng.choice(literals)
        pieces.append(value)
    pieces.append(JWT_HEAD + "not-a-literal")  # a prefix false positive candidate
    text = "".join(pieces)
    whole = journal.text(text)
    for chunks in splits(len(text), rng):
        emitted, _ = stream(text, journal, lambda: hold, chunks)
        assert emitted == whole


def test_review_counterexample_and_overlaps_stay_whole(tmp_path):
    for literals, text in ((["abcdefghijklmnop", "klmnopqrstuvwxyz"], "abcdefghijklmnopqrstuvwxyz"),
                           (["A" * 26], "A" * 100),
                           (["x" * 20 + "y" * 10, "y" * 10 + "z" * 20], "x" * 20 + "y" * 10 + "z" * 20)):
        journal = journal_with(literals)
        control = control_dir(tmp_path)
        stream_prefix.remember(control, journal, literals)
        hold = policy(journal, control)
        for chunks in ([1] * len(text), [7] * (len(text) // 7) + [len(text) % 7]):
            emitted, _ = stream(text, journal, lambda: hold, [c for c in chunks if c])
            assert emitted == journal.text(text)


# --- holdback bounds (r4.1: only for windows with no span crossing the cut) -----------------------------------------

def test_covered_long_literals_only_hold_p_minus_1(tmp_path):
    token = JWT_HEAD + "a" * 1640
    journal = journal_with([token, "short-id"])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [token])
    hold = policy(journal, control)
    assert (hold.legacy, hold.covered, hold.short) == (0, True, len("short-id"))
    text = "an ordinary answer without any recorded literal " * 20
    _, peaks = stream(text, journal, lambda: hold, [1] * len(text))
    assert max(peaks) <= P - 1


def test_a_legacy_literal_holds_its_length_minus_1(tmp_path):
    token = JWT_HEAD + "b" * 1640
    journal = journal_with([token])
    control = control_dir(tmp_path)  # no fingerprint written: legacy
    hold = policy(journal, control)
    assert hold.legacy == len(token) and not hold.covered
    text = "x" * 3000
    _, peaks = stream(text, journal, lambda: hold, [1] * len(text))
    assert max(peaks) == len(token) - 1


def test_an_in_progress_covered_literal_is_held_from_its_start_and_a_false_prefix_is_released(tmp_path):
    token = JWT_HEAD + "c" * 200
    journal = journal_with([token])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [token])
    hold = policy(journal, control)
    head = "hello "
    assert hold.start(head + token[:100]) == len(head)  # the candidate's start: nothing of it leaves yet
    false = head + token[:P] + "Z" * 300               # the same prefix, but no literal follows
    assert hold.start(false) == len(false) - (P - 1)   # released once a + lmax <= n
    assert journal.text(false) == false


@pytest.mark.parametrize("n", [0, 1, P - 1, P])
def test_cut_start_boundaries(tmp_path, n):
    token = JWT_HEAD + "d" * 50
    journal = journal_with([token, "q"])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [token])
    hold = policy(journal, control)
    window = "w" * n
    assert hold.start(window) == max(0, min(n - (P - 1), n))  # n - c0 = min(n, max(...)) (review r4.1)
    assert 0 <= hold.start(window) <= n


# --- fail-closed and the fingerprint file ---------------------------------------------------------------------------

def tamper(path, change):
    payload = json.loads(path.read_text())
    change(payload)
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)


@pytest.mark.parametrize("damage", ["missing", "mac", "owner", "prefixLength", "truncated", "filter-type",
                                    "length-range", "digest", "extra-field"])
def test_a_damaged_fingerprint_file_makes_every_long_literal_legacy(tmp_path, damage):
    token = JWT_HEAD + "e" * 100
    journal = journal_with([token, "short"])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [token])
    path = control / stream_prefix.FILE
    assert policy(journal, control).legacy == 0
    if damage == "missing":
        path.unlink()
    elif damage == "mac":
        tamper(path, lambda p: p.__setitem__("mac", "0" * 64))
    elif damage == "owner":
        tamper(path, lambda p: p.__setitem__("owner", "someone-else"))
    elif damage == "prefixLength":
        tamper(path, lambda p: p.__setitem__("prefixLength", 8))
    elif damage == "truncated":
        path.write_text(path.read_text()[:40])
    elif damage == "filter-type":
        tamper(path, lambda p: p["entries"][0].__setitem__(0, "1"))
    elif damage == "length-range":
        tamper(path, lambda p: p["entries"][0].__setitem__(2, P))
    elif damage == "digest":
        tamper(path, lambda p: p["entries"][0].__setitem__(1, "xyz"))
    elif damage == "extra-field":
        tamper(path, lambda p: p.__setitem__("note", 1))
    hold = policy(journal, control)
    assert hold.legacy == len(token) and not hold.covered and hold.short == len("short")


def test_a_file_whose_mac_is_wrong_is_legacy_even_if_its_entries_match(tmp_path):
    """Review r4 minor: a structural match (what journal_lengths v2 can count) is not coverage at runtime."""
    token = JWT_HEAD + "f" * 100
    journal = journal_with([token])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [token])
    path = control / stream_prefix.FILE
    tamper(path, lambda p: p.__setitem__("mac", "f" * 64))
    entries = json.loads(path.read_text())["entries"]
    assert any(entry[2] == len(token) and entry[3] == journal.digest(token) for entry in entries)  # structurally there
    assert policy(journal, control).legacy == len(token)


def test_remember_is_a_no_op_for_known_literals_and_recovers_a_damaged_file(tmp_path):
    token = JWT_HEAD + "g" * 100
    journal = journal_with([token])
    control = control_dir(tmp_path)
    assert stream_prefix.remember(control, journal, [token]) is True
    path = control / stream_prefix.FILE
    before = os.stat(path)
    state = policy(journal, control).state
    assert stream_prefix.remember(control, journal, [token]) is False  # no rewrite, no generation change
    assert (os.stat(path).st_ino, os.stat(path).st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert policy(journal, control) == policy(journal, control) and policy(journal, control).state == state
    path.write_text("{")
    os.chmod(path, 0o600)
    assert stream_prefix.remember(control, journal, [token]) is True and policy(journal, control).legacy == 0
    assert token[:P] not in path.read_text() and token not in path.read_text()  # no plaintext


def test_a_failed_fingerprint_write_leaves_the_literal_legacy_and_never_raises(tmp_path, monkeypatch):
    token = JWT_HEAD + "h" * 100
    journal = journal_with([token])
    control = control_dir(tmp_path)
    monkeypatch.setattr(stream_prefix, "_write", lambda path, payload: (_ for _ in ()).throw(OSError(28, "full")))
    assert stream_prefix.remember(control, journal, [token]) is False
    assert policy(journal, control).legacy == len(token)


def test_a_symlinked_or_shared_file_is_not_read(tmp_path):
    token = JWT_HEAD + "i" * 100
    journal = journal_with([token])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [token])
    path = control / stream_prefix.FILE
    os.chmod(path, 0o644)
    assert policy(journal, control).legacy == len(token)
    os.chmod(path, 0o600)
    target = control / "elsewhere.json"
    path.rename(target)
    path.symlink_to(target)
    assert policy(journal, control).legacy == len(token)


def test_a_change_of_the_file_changes_the_policy_identity(tmp_path):
    """r4 §3: the generation includes the fingerprint file state, so a plan made before a change is discarded."""
    one, two = JWT_HEAD + "j" * 40, JWT_HEAD + "k" * 60
    journal = journal_with([one, two])
    control = control_dir(tmp_path)
    stream_prefix.remember(control, journal, [one])
    before = policy(journal, control)
    stream_prefix.remember(control, journal, [two])
    after = policy(journal, control)
    assert before != after and before.legacy == len(two) and after.legacy == 0


def test_current_secrets_not_yet_covered_count_as_legacy(tmp_path):
    token = JWT_HEAD + "m" * 100
    journal = journal_with([])
    control = control_dir(tmp_path)
    assert policy(journal, control, [token]).legacy == len(token)
    journal.observe([token])
    stream_prefix.remember(control, journal, [token])
    assert policy(journal, control, [token]).legacy == 0


def test_no_literal_holds_nothing():
    assert HoldPolicy.none().start("abc") == 3
    assert Holdback.plan("abc", [], HoldPolicy.none(), False, False) == ("abc", 3, False)
    assert Holdback.plan("abc", [(0, 3)], HoldPolicy.none(), True, False)[0] == MARKER


# --- the auth modes write fingerprints after the journal ---------------------------------------------------------

def test_service_install_fingerprints_the_current_token_and_an_older_one_stays_legacy(tmp_path):
    from test_service_access import setup, refreshed
    auth, home, envelope = setup(tmp_path)
    (auth.control / stream_prefix.FILE).unlink(missing_ok=True)
    auth.install(envelope)
    auth.attach(home)
    hold = policy(auth.journal, auth.control)
    assert hold.legacy == 0 and hold.covered  # the access token (> P) is covered after install
    update = refreshed(envelope)
    auth.install(update)
    hold = policy(auth.journal, auth.control)
    assert hold.legacy == 0  # both tokens were installed (plaintext in memory) under r4: both covered
    contents = (auth.control / stream_prefix.FILE).read_text()
    assert envelope["accessToken"][:P] not in contents and update["accessToken"][:P] not in contents
    # A token recorded before r4 (no fingerprint) and never installed again stays legacy.
    with auth.store.transaction() as db:
        auth.journal.remember(db, ["L" * 1676])
    assert policy(auth.journal, auth.control).legacy == 1676
    auth.close()
