"""Mocked controller module for the #61 W5 harness: lets the real PROOF body run. W5_FAIL_STEP (build, bound,
namespace, pod, node) raises there; W5_POD_UID makes the Pod lookup return that UID (else absent). Every call is
appended to /mock/w5-proof-calls.log."""
import os
from contextlib import contextmanager

LOG = '/mock/w5-proof-calls.log'


def note(step):
    with open(LOG, 'a') as stream:
        stream.write(step + '\n')
    if os.environ.get('W5_FAIL_STEP') == step:
        raise PermissionError('planted failure with SECRET-DETAIL-NEVER-PRINT')


class Runtime:
    class pods:
        client = object()

    def namespace(self):
        note('namespace')


class Node:
    def identity(self, client, now):
        note('node')
        return {'nodeName': os.environ.get('W5_NODE', 'devvm'), 'nodeUid': 'u', 'bootId': 'b'}


class Pool:
    @contextmanager
    def bound(self, owner):
        assert str(owner) == '00000000-0000-4000-8000-000000005804'
        note('bound')
        yield Runtime()


class Service:
    observer_pool = Pool()
    node = Node()

    def pod(self, runtime):
        note('pod')
        uid = os.environ.get('W5_POD_UID')
        return {'metadata': {'uid': uid}} if uid else None

    def now(self):
        return 0


def build():
    note('build')
    return Service(), {}, None
