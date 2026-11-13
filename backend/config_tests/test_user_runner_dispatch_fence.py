# #90 review d319fcb counterexample (review: /tmp/issue90-d319/test_dispatch_fence.py), added unchanged as a
# regression test on PM request; only this header is new. Needs PostgreSQL (review's suite, like
# test_user_runner_runtime.py).
from threading import Thread, Event
from app.conversations import Runner
from config_tests.test_user_runner_runtime import runtime, client, research_schema, schema, admin, plan

def test_stop_cannot_commit_between_check_and_dispatch(runtime, monkeypatch):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    stopped = Event()
    errors = []
    threads = []
    observed = []
    def stop():
        try:
            rt.stop(owner)
            stopped.set()
        except Exception as exc:
            errors.append(exc)
    def before_network(self, method, path, body=None):
        thread = Thread(target=stop)
        threads.append(thread)
        thread.start()
        observed.append(stopped.wait(1.0))
        return {'podUid': server.pod['metadata']['uid']}
    monkeypatch.setattr(Runner, 'call', before_network)
    rt.runner(owner).call('POST', '/sessions/example/turn', {})
    for thread in threads:
        thread.join(5)
    assert not errors
    assert stopped.is_set()
    assert observed == [False], 'durable stop committed before checked POST dispatch'
