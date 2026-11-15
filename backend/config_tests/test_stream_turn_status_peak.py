"""#95 B2 / B3: runner_stream_summary carries the native turn status apart from the stream-side stop, and
subscribersPeak counts subscribers that were already attached when the turn began. Pure: no runner, no network."""
import json
import logging
import uuid

import pytest

from runner.stream import Hub

SESSION = str(uuid.uuid4())


@pytest.fixture
def summaries(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    def read():
        return [json.loads(r.getMessage().partition(' ')[2]) for r in caplog.records
                if r.getMessage().startswith('runner_stream_summary ')]
    return read


def turn(hub, session=SESSION):
    request = str(uuid.uuid4())
    hub.begin(session, request)
    hub.bind(thread='thread-1', turn_id='turn-1')
    return request


@pytest.mark.parametrize('native, logged', [('completed', 'completed'), ('interrupted', 'interrupted'),
                                            ('failed', 'failed'), ('SECRET-odd', 'other'), (None, None)])
def test_the_summary_carries_the_native_turn_status(summaries, caplog, native, logged):
    hub = Hub(None, background=False)
    request = turn(hub)
    hub.complete('idle', native, None)
    line, = summaries()
    assert line['requestId'] == request and line['turnStatus'] == logged
    assert line['stopped'] is None  # a user stop is not a stream-side stop
    assert 'SECRET' not in caplog.text


def test_a_stream_side_stop_and_a_user_stop_are_apart(summaries):
    hub = Hub(None, background=False)
    turn(hub)
    with hub.lock:
        hub.stop(hub.active, 'budget')
    hub.complete('idle', 'completed', None)
    line, = summaries()
    assert (line['stopped'], line['turnStatus']) == ('budget', 'completed')


def test_a_subscriber_attached_before_the_turn_counts(summaries):
    """The production case: the browser opens /events, then sends; the peak was 0 before #95."""
    hub = Hub(None, background=False)
    subscription, _ = hub.subscribe(SESSION, None)
    assert subscription is not None
    turn(hub)
    hub.complete('idle', 'completed', None)
    assert summaries()[-1]['subscribersPeak'] == 1


def test_subscribers_during_the_turn_raise_the_peak_and_leaving_does_not_lower_it(summaries):
    hub = Hub(None, background=False)
    first, _ = hub.subscribe(SESSION, None)
    turn(hub)
    second, _ = hub.subscribe(SESSION, None)
    third, _ = hub.subscribe(SESSION, None)
    hub.unsubscribe(second)
    hub.unsubscribe(third)
    hub.complete('idle', 'interrupted', None)
    line = summaries()[-1]
    assert (line['subscribersPeak'], line['turnStatus']) == (3, 'interrupted')


def test_no_subscriber_is_zero_and_a_left_one_does_not_count_for_the_next_turn(summaries):
    hub = Hub(None, background=False)
    turn(hub)
    hub.complete('idle', 'completed', None)
    assert summaries()[-1]['subscribersPeak'] == 0
    gone, _ = hub.subscribe(SESSION, None)
    hub.unsubscribe(gone)
    turn(hub)
    hub.complete('idle', 'completed', None)
    assert summaries()[-1]['subscribersPeak'] == 0
