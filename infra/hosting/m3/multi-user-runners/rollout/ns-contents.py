"""Read-only: prove what a namespace holds, through the API (#61).

    python3 -I ns-contents.py <namespace>

Lists every namespaced, listable resource type of every discovered group version in <namespace>, one complete
page each, and compares the result with the defaults a fresh namespace gets: serviceaccounts/default and
configmaps/kube-root-ca.crt (plus events, which are not content). The one group version allowed to fail discovery
is metrics.k8s.io/v1beta1: an aggregated API that computes metrics and stores no objects, unavailable on both the
development VM and production (no TCP10250 grant, #13). Any other discovery or list failure, malformed response
or continuation token is a failed proof.

rc: 0 only the defaults; 3 the proof failed (lookup, discovery, malformed or paginated answer); 4 other objects
are present (listed). Prints fixed fields: group version, resource and object names only.
"""
import json
import subprocess
import sys

K = ['kubectl']
ALLOWED_FAILING = {'metrics.k8s.io/v1beta1'}
DEFAULTS = {('v1', 'serviceaccounts', 'default'), ('v1', 'configmaps', 'kube-root-ca.crt')}
NOT_CONTENT = {('v1', 'events'), ('events.k8s.io/v1', 'events')}


class Failed(Exception):
    pass


def raw(path):
    try:
        done = subprocess.run(K + ['get', '--raw', path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        return None


def resources_of(doc, gv):
    if not isinstance(doc, dict) or not isinstance(doc.get('resources'), list):
        raise Failed('discovery of %s has no resource list' % gv)
    for resource in doc['resources']:
        if not (isinstance(resource, dict) and isinstance(resource.get('name'), str) and resource['name']
                and isinstance(resource.get('namespaced'), bool) and isinstance(resource.get('verbs'), list)):
            raise Failed('discovery of %s has a malformed resource entry' % gv)
    return doc['resources']


def contents(namespace):
    versions = [('v1', '/api/v1', resources_of(raw('/api/v1'), 'v1'))]
    groups = raw('/apis')
    if not isinstance(groups, dict) or not isinstance(groups.get('groups'), list) or not groups['groups']:
        raise Failed('discovery of /apis failed or has no group list')
    skipped = []
    for group in groups['groups']:
        if not isinstance(group, dict) or not isinstance(group.get('versions'), list) or not group['versions']:
            raise Failed('/apis has a group without versions')
        for version in group['versions']:
            gv = version.get('groupVersion') if isinstance(version, dict) else None
            if not isinstance(gv, str) or '/' not in gv:
                raise Failed('/apis has a malformed groupVersion')
            doc = raw('/apis/' + gv)
            if doc is None and gv in ALLOWED_FAILING:
                skipped.append(gv)
                continue
            versions.append((gv, '/apis/' + gv, resources_of(doc, gv)))
    found, listed = [], 0
    for gv, base, resources in versions:
        for resource in resources:
            if not resource['namespaced'] or '/' in resource['name'] or 'list' not in resource['verbs']:
                continue
            if (gv, resource['name']) in NOT_CONTENT:
                continue
            items = raw('%s/namespaces/%s/%s?limit=500' % (base, namespace, resource['name']))
            if not isinstance(items, dict) or not isinstance(items.get('items'), list) \
                    or not isinstance(items.get('metadata'), dict):
                raise Failed('listing %s %s failed or is malformed' % (gv, resource['name']))
            if items['metadata'].get('continue'):
                raise Failed('listing %s %s is paginated' % (gv, resource['name']))
            listed += 1
            for item in items['items']:
                name = item.get('metadata', {}).get('name') if isinstance(item, dict) else None
                found.append((gv, resource['name'], name))
    if listed == 0:
        raise Failed('no resource type was listed')
    return found, listed, len(versions), skipped


def main(argv):
    if len(argv) != 2 or not argv[1]:
        print('usage: ns-contents.py <namespace>')
        return 3
    try:
        found, listed, versions, skipped = contents(argv[1])
    except Failed as failure:
        print('proof failed: %s' % failure)
        print('result rc=3')
        return 3
    print('enumerated %d group versions, %d resource types; skipped (failing, allowed, stores no objects): %s'
          % (versions, listed, ', '.join(skipped) or 'none'))
    other = sorted(set(found) - DEFAULTS)
    for gv, resource, name in sorted(found):
        print('%s %s %s/%s' % ('default' if (gv, resource, name) in DEFAULTS else 'OTHER', gv, resource, name))
    if other:
        print('result rc=4')
        return 4
    print('only the defaults')
    print('result rc=0')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
