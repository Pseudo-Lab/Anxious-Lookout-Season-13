"""Offline lifecycle scenarios; no claim of DB/controller/native integration."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from runner.lifecycle import (State, Phase, Fence, Refused, entry, capacity, observed,
                              admitted_request, idle_stop, stopped)

START = datetime(2026, 10, 14, tzinfo=timezone.utc)


def new():
    return State(uuid4(), uuid4(), START)


def ready():
    state = entry(new(), eligible=True, replayed=False, now=START).state
    return observed(state, Fence.of(state), pod_uid=uuid4(), ready=True,
                    active_requests=0, ambiguous=False, now=START)


def test_entry_tabs_and_durable_replay_coalesce_without_activity_extension():
    state = new()
    first = entry(state, eligible=True, replayed=False, now=START)
    for replay in (False, True):
        again = entry(first.state, eligible=True, replayed=replay, now=START + timedelta(minutes=5))
        assert again.state == first.state and again.work is None
    live = observed(first.state, Fence.of(first.state), pod_uid=uuid4(), ready=True,
                    active_requests=0, ambiguous=False, now=START)
    replay = entry(live, eligible=True, replayed=True, now=START + timedelta(minutes=10))
    assert replay.state == live and replay.work is None
    revisit = entry(live, eligible=True, replayed=False, now=START + timedelta(minutes=10))
    assert revisit.state.last_activity == START + timedelta(minutes=10)
    assert revisit.state.pod_uid == live.pod_uid and revisit.state.generation == live.generation
    assert revisit.work is None


def test_revoked_eligibility_refuses_even_receipt_replay():
    with pytest.raises(Refused): entry(ready(), eligible=False, replayed=True, now=START)


def test_capacity_wait_preserves_identity_priority_and_activity():
    first = entry(new(), eligible=True, replayed=False, now=START).state
    waiting = capacity(first, Fence.of(first), eligible=True, admitted=False)
    retry = entry(waiting.state, eligible=True, replayed=False, now=START + timedelta(minutes=25))
    assert retry.state == waiting.state and retry.work is None
    resumed = capacity(waiting.state, Fence.of(waiting.state), eligible=True, admitted=True)
    assert resumed.state.state_id == first.state_id
    assert resumed.state.generation == first.generation and resumed.state.last_activity == START
    assert resumed.work.kind == "ensure"
    with pytest.raises(Refused): capacity(waiting.state, Fence.of(waiting.state), eligible=False, admitted=True)


def test_idle_boundary_reclaims_only_pod_and_explicit_revisit_reuses_state():
    live = ready()
    with pytest.raises(Refused):
        idle_stop(live, Fence.of(live), now=START + timedelta(minutes=30, microseconds=-1), quiescence_verified=True)
    stop = idle_stop(live, Fence.of(live), now=START + timedelta(minutes=30), quiescence_verified=True)
    assert stop.work.kind == "stop-pod-only" and stop.work.fence.pod_uid == live.pod_uid
    retained = stopped(stop.state, Fence.of(stop.state), terminated_pod_uid=live.pod_uid, termination_verified=True)
    assert retained.work is None and retained.state.pod_uid is None
    assert retained.state.owner == live.owner and retained.state.state_id == live.state_id
    assert retained.state.last_activity == live.last_activity
    revisit = entry(retained.state, eligible=True, replayed=False, now=START + timedelta(hours=1))
    assert revisit.state.state_id == live.state_id and revisit.state.generation == live.generation + 1
    assert revisit.work.kind == "ensure"


@pytest.mark.parametrize("active,ambiguous", [(1,False), (0,True), (1,True)])
def test_busy_or_ambiguous_never_reclaimed_even_after_a_day(active, ambiguous):
    state = replace(ready(), active_requests=active, ambiguous=ambiguous)
    with pytest.raises(Refused):
        idle_stop(state, Fence.of(state), now=START + timedelta(days=1), quiescence_verified=True)


def test_old_observation_cannot_erase_new_reservation():
    live = ready()
    before_read = Fence.of(live)
    busy = admitted_request(live, before_read, now=START + timedelta(minutes=1))
    with pytest.raises(Refused):
        observed(busy, before_read, pod_uid=live.pod_uid, ready=True,
                 active_requests=0, ambiguous=False, now=START + timedelta(minutes=2))
    assert busy.active_requests == 1
    with pytest.raises(Refused): idle_stop(busy, Fence.of(busy), now=START + timedelta(hours=1), quiescence_verified=True)


def test_completion_sets_idle_origin_but_repeated_reads_do_not():
    live = ready()
    busy = admitted_request(live, Fence.of(live), now=START)
    complete_at = START + timedelta(hours=1)
    done = observed(busy, Fence.of(busy), pod_uid=live.pod_uid, ready=True,
                    active_requests=0, ambiguous=False, now=complete_at)
    read_at = complete_at + timedelta(minutes=29)
    read = observed(done, Fence.of(done), pod_uid=live.pod_uid, ready=True,
                    active_requests=0, ambiguous=False, now=read_at)
    assert done.last_activity == read.last_activity == complete_at
    with pytest.raises(Refused): idle_stop(read, Fence.of(read), now=read_at, quiescence_verified=True)


@pytest.mark.parametrize("field,value", [("owner",uuid4()), ("state_id",uuid4()),
                                        ("generation",99), ("revision",99), ("pod_uid",uuid4())])
def test_foreign_or_stale_fences_cannot_stop_a_live_runner(field, value):
    live = ready()
    with pytest.raises(Refused):
        idle_stop(live, replace(Fence.of(live), **{field:value}), now=START + timedelta(hours=1), quiescence_verified=True)


def test_stopping_fences_new_entries_and_admissions_until_actual_termination():
    live = ready()
    stopping = idle_stop(live, Fence.of(live), now=START + timedelta(hours=1), quiescence_verified=True).state
    with pytest.raises(Refused): entry(stopping, eligible=True, replayed=False, now=START + timedelta(hours=1))
    with pytest.raises(Refused): admitted_request(stopping, Fence.of(stopping), now=START + timedelta(hours=1))
    with pytest.raises(Refused): stopped(stopping, Fence.of(stopping), terminated_pod_uid=live.pod_uid, termination_verified=False)
    with pytest.raises(Refused): stopped(stopping, Fence.of(stopping), terminated_pod_uid=uuid4(), termination_verified=True)


def test_unknown_quiescence_unreachable_and_wrong_pod_never_reclaimed_or_adopted():
    live = ready()
    with pytest.raises(Refused): idle_stop(live, Fence.of(live), now=START + timedelta(hours=1), quiescence_verified=False)
    unavailable = replace(live, phase=Phase.UNREACHABLE)
    with pytest.raises(Refused): idle_stop(unavailable, Fence.of(unavailable), now=START + timedelta(hours=1), quiescence_verified=True)
    with pytest.raises(Refused):
        observed(live, Fence.of(live), pod_uid=uuid4(), ready=True, active_requests=0, ambiguous=False, now=START)


@pytest.mark.parametrize("now", [START.replace(tzinfo=None), START - timedelta(seconds=1)])
def test_invalid_or_reversed_clock_refused(now):
    state = ready()
    with pytest.raises(Refused): idle_stop(state, Fence.of(state), now=now, quiescence_verified=True)


@pytest.mark.parametrize("field,value", [("active_requests",True), ("active_requests",-1),
                                        ("ambiguous",1), ("generation",True), ("revision",-1)])
def test_malformed_persisted_counters_refused(field, value):
    with pytest.raises(Refused): replace(ready(), **{field:value})
