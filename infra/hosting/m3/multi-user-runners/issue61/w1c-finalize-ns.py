"""#61 dev-VM gate W1c: finalize the one empty namespace W2 created and its rollback deleted, run once by the user
as root in the guest after review cleared this SHA-256 (user decision 2026-11-02, option 1).

    python3 -I /root/issue61/w1c-finalize-ns.py

Why: `runner-control` (uid 54a486c3-…) is Terminating with all content removed, but namespace finalization waits
on API discovery, which fails because the metrics.k8s.io APIService has no endpoints (no TCP10250 grant, #13).
Only this namespace is touched; shell-pilot/shell-full, the APIService and the firewall are not.

1. precheck (read-only; any failure refuses, nothing changed):
   - the namespace exists with exactly that name and UID, phase Terminating, a deletionTimestamp, and
     spec.finalizers == ["kubernetes"];
   - its conditions say NamespaceContentRemaining=False, NamespaceDeletionContentFailure=False and
     NamespaceFinalizersRemaining=False (each present); NamespaceDeletionDiscoveryFailure, if True, names only
     metrics.k8s.io/v1beta1;
   - authoritative enumeration through the API: GET /api/v1 and every group version from GET /apis; every
     namespaced resource with the list verb (subresources excluded) is listed in runner-control and must be
     empty. The only group version allowed to fail is metrics.k8s.io/v1beta1 (an aggregated API that computes
     metrics and stores no objects); any other discovery or list failure refuses.
2. finalize: PUT /api/v1/namespaces/runner-control/finalize with the object as read (its uid and
   resourceVersion, so a replaced or changed namespace is refused) and spec.finalizers = [].
3. postcheck (read-only, bounded 60 s): the namespace is gone. Still present with the same UID, another UID,
   or a failed lookup is not reported as done.
rc: 0 finalized and gone; 3 precheck refused, nothing changed; 5 the finalize call failed or its answer is
unclear (do not rerun; back checks read-only); 8 finalize answered but the namespace is not proved gone.
Fixed messages only.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

K3S = '/usr/local/bin/k3s'
NS = 'runner-control'
UID = '54a486c3-0750-411b-82bb-70a39b3c80b4'
ALLOWED_FAILING = {'metrics.k8s.io/v1beta1'}
WAIT = max(1, min(60, int(os.environ.get('W1C_WAIT_SECONDS', 60))))  # harness may only shorten it


class Stop(Exception):
    def __init__(self, rc, message):
        super().__init__(message)
        self.rc, self.message = rc, message


def say(text):
    print(text, flush=True)


def raw(path, timeout=30):
    """(ok, parsed JSON) of a GET through the API server; never raises."""
    try:
        done = subprocess.run([K3S, 'kubectl', 'get', '--raw', path], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except Exception:
        return False, None
    if done.returncode != 0:
        return False, None
    try:
        return True, json.loads(done.stdout)
    except ValueError:
        return False, None


def namespace():
    """The namespace object, None if absent (404), or raise on a failed lookup."""
    try:
        done = subprocess.run([K3S, 'kubectl', 'get', 'namespace', NS, '-o', 'json', '--ignore-not-found'],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
    except Exception:
        raise Stop(3, 'namespace lookup failed') from None
    if done.returncode != 0:
        raise Stop(3, 'namespace lookup failed')
    return json.loads(done.stdout) if done.stdout.strip() else None


def precheck():
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    ns = namespace()
    if ns is None:
        raise Stop(3, 'precheck: namespace %s is absent; nothing to finalize' % NS)
    meta, status = ns['metadata'], ns.get('status') or {}
    if meta.get('uid') != UID:
        raise Stop(3, 'precheck: namespace %s has uid %s, not %s' % (NS, meta.get('uid'), UID))
    if status.get('phase') != 'Terminating' or not meta.get('deletionTimestamp'):
        raise Stop(3, 'precheck: namespace %s is %s (deletionTimestamp %s), not Terminating'
                   % (NS, status.get('phase'), meta.get('deletionTimestamp')))
    if (ns.get('spec') or {}).get('finalizers') != ['kubernetes']:
        raise Stop(3, 'precheck: namespace finalizers are %s' % (ns.get('spec') or {}).get('finalizers'))
    conditions = {c.get('type'): c for c in status.get('conditions', [])}
    for kind in ('NamespaceContentRemaining', 'NamespaceDeletionContentFailure', 'NamespaceFinalizersRemaining'):
        if (conditions.get(kind) or {}).get('status') != 'False':
            raise Stop(3, 'precheck: condition %s is %s' % (kind, (conditions.get(kind) or {}).get('status', 'missing')))
    discovery = conditions.get('NamespaceDeletionDiscoveryFailure') or {}
    if discovery.get('status') == 'True':
        message = discovery.get('message', '')
        if '1 failing' not in message or 'metrics.k8s.io/v1beta1' not in message:
            raise Stop(3, 'precheck: discovery failure is not only metrics.k8s.io/v1beta1')
    say('namespace %s uid %s Terminating since %s, resourceVersion %s; conditions ok'
        % (NS, UID, meta['deletionTimestamp'], meta.get('resourceVersion')))
    return ns


def resources_of(doc, gv):
    """The resource list of a discovery document; any missing or malformed field refuses."""
    if not isinstance(doc, dict) or not isinstance(doc.get('resources'), list):
        raise Stop(3, 'precheck: discovery of %s has no resource list' % gv)
    for resource in doc['resources']:
        if not (isinstance(resource, dict) and isinstance(resource.get('name'), str) and resource['name']
                and isinstance(resource.get('namespaced'), bool) and isinstance(resource.get('verbs'), list)):
            raise Stop(3, 'precheck: discovery of %s has a malformed resource entry' % gv)
    return doc['resources']


def enumerate_content():
    """Every namespaced, listable resource type must list empty in one complete page.

    The only exception is metrics.k8s.io/v1beta1: an aggregated API that computes metrics and stores no objects,
    and whose missing endpoints are the reason this namespace cannot finalize. Every other discovery or list
    failure, malformed response or continuation refuses."""
    versions = []
    ok, core = raw('/api/v1')
    if not ok:
        raise Stop(3, 'precheck: discovery of /api/v1 failed')
    versions.append(('v1', '/api/v1', resources_of(core, 'v1')))
    ok, groups = raw('/apis')
    if not ok or not isinstance(groups, dict) or not isinstance(groups.get('groups'), list) or not groups['groups']:
        raise Stop(3, 'precheck: discovery of /apis failed or has no group list')
    skipped = []
    for group in groups['groups']:
        if not isinstance(group, dict) or not isinstance(group.get('versions'), list) or not group['versions']:
            raise Stop(3, 'precheck: /apis has a group without versions')
        for version in group['versions']:
            gv = version.get('groupVersion') if isinstance(version, dict) else None
            if not isinstance(gv, str) or '/' not in gv:
                raise Stop(3, 'precheck: /apis has a malformed groupVersion')
            ok, doc = raw('/apis/' + gv)
            if not ok:
                if gv in ALLOWED_FAILING:
                    skipped.append(gv)
                    continue
                raise Stop(3, 'precheck: discovery of %s failed' % gv)
            versions.append((gv, '/apis/' + gv, resources_of(doc, gv)))
    listed = 0
    for gv, base, resources in versions:
        for resource in resources:
            if not resource['namespaced'] or '/' in resource['name'] or 'list' not in resource['verbs']:
                continue
            ok, items = raw('%s/namespaces/%s/%s?limit=500' % (base, NS, resource['name']))
            if not ok or not isinstance(items, dict) or not isinstance(items.get('items'), list) \
                    or not isinstance(items.get('metadata'), dict):
                raise Stop(3, 'precheck: listing %s %s failed or is malformed' % (gv, resource['name']))
            if items['metadata'].get('continue'):
                raise Stop(3, 'precheck: listing %s %s is paginated; not proved empty' % (gv, resource['name']))
            listed += 1
            left = [item.get('metadata', {}).get('name') if isinstance(item, dict) else '?' for item in items['items']]
            if left:
                raise Stop(3, 'precheck: %s %s still holds %s' % (gv, resource['name'], left))
    say('enumerated %d group versions, %d namespaced resource types listed empty; skipped (failing, allowed, '
        'stores no objects): %s' % (len(versions), listed, ', '.join(skipped) or 'none'))
    if listed == 0:
        raise Stop(3, 'precheck: no resource type was listed')


def finalize(ns):
    body = json.loads(json.dumps(ns))
    body['spec']['finalizers'] = []  # uid and resourceVersion stay as read: the API refuses a changed object
    with tempfile.NamedTemporaryFile('w', dir='/run', prefix='issue61-w1c-', suffix='.json', delete=False) as stream:
        json.dump(body, stream)
        path = stream.name
    try:
        done = subprocess.run([K3S, 'kubectl', 'replace', '--raw', '/api/v1/namespaces/%s/finalize' % NS, '-f', path],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False)
    except Exception:
        raise Stop(5, 'finalize call failed or timed out: do not rerun; back checks read-only') from None
    finally:
        os.unlink(path)
    if done.returncode != 0:
        raise Stop(5, 'finalize refused or failed (conflict, changed object or API error): do not rerun; back checks')
    say('finalize answered')


def gone():
    deadline = time.monotonic() + WAIT
    while True:
        try:
            ns = namespace()
        except Stop:
            raise Stop(8, 'postcheck: namespace lookup failed; not reported as done') from None
        if ns is None:
            say('namespace %s is gone' % NS)
            return
        if ns['metadata'].get('uid') != UID:
            raise Stop(8, 'postcheck: namespace %s has another uid %s now' % (NS, ns['metadata'].get('uid')))
        if time.monotonic() >= deadline:
            raise Stop(8, 'postcheck: namespace %s (uid %s) still present after %d s' % (NS, UID, WAIT))
        time.sleep(min(3, max(0.1, deadline - time.monotonic())))


def main():
    stage = 'precheck'
    try:
        ns = precheck()
        enumerate_content()
        stage = 'finalize'
        finalize(ns)
        gone()
    except Stop as stop:
        say(stop.message)
        say('result rc=%d' % stop.rc)
        return stop.rc
    except Exception:
        rc = 3 if stage == 'precheck' else 5  # Before the finalize call nothing has changed.
        say('unexpected error in %s (no detail printed): do not rerun; back checks read-only' % stage)
        say('result rc=%d' % rc)
        return rc
    say('result rc=0')
    return 0


if __name__ == '__main__':
    sys.exit(main())
