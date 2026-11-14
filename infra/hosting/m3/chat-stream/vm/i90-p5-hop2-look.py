"""#90 P5 r6 diagnosis (PM / review 2026-11-15 after turns-r6 stopped at "S4a: reader hop2 did not open (None)"),
development VM only, READ ONLY: why the hop ② reader (review's client in a `--network host` container, connecting to the
`api` Service cluster IP :8080) got no headers. Nothing is written. Run as root on the guest with the reviewed bytes on
stdin:

    sudo -n python3 -I -B - < i90-p5-hop2-look.py

Output, JSON lines, fixed keys only (no URL, cookie, conversation id, Pod name or IP is printed):
- `client`: from the one `i90-p5-turns-r6-<UTC>` evidence run, the hop ② reader's JSONL: record count per kind (fixed
  kinds), whether a `headers` record exists, and its `end` record's reason / errorType (fixed lists, else `other`) and
  elapsedMs; whether a hop ③ reader file exists for S4a (it should not: readers open one by one);
- `cilium`: `allow-localhost` and `kube-proxy-replacement` from ConfigMap kube-system/cilium-config (fixed values, else
  `other`, or `missing`);
- `endpoint`: the api Pods' CiliumEndpoints (identity label k8s:app=hosting-api): count, their numeric identity, and the
  ingress policy enforcement fields as recorded (typed; missing kept apart). The identity labels of the single api
  endpoint (not printed) decide which policies select it;
- `policies`: every CiliumNetworkPolicy in m2-hosting and every CiliumClusterwideNetworkPolicy: its name (fixed list, else
  `other`), whether it selects the api endpoint (`true` / `false` / `unknown`: matchExpressions, another label source,
  or not exactly one api endpoint), and per ingress / ingressDeny rule its sources (`endpoints:traefik`,
  `endpoints:user-runner`, `endpoints:other`, `entities:<fixed>`, `cidr`, `other`, or `any-source` for a rule with no
  peer field) and ports (numbers, or `any`);
- `hostTo8080`: the rules of policies that select (or may select) the api endpoint whose sources include the host
  (`entities:host`, `entities:all` or `any-source`) on 8080 or any port; `admitted` counts ingress (allow) rules only.
This reads the policy objects; it does not read the datapath (no cilium-dbg, no flow log: hubble is disabled).
rc: 0 printed; 3 refused (not root, or a cluster lookup failed).
"""
import json
import os
import re
import stat
import subprocess
import sys

EVIDENCE = '/root/issue90/evidence'
K3S = '/usr/local/bin/k3s'
RUN = re.compile(r'i90-p5-turns-r6-\d{8}T\d{6}Z')
KINDS = ('headers', 'frame', 'trace', 'heartbeat', 'reader_paused', 'end')
REASONS = ('probe_error', 'http_error', 'local_deadline', 'local_interrupt', 'local_close', 'peer_eof')
ERRORS = ('TimeoutError', 'ConnectionRefusedError', 'ConnectionResetError', 'ConnectionAbortedError', 'OSError',
          'BrokenPipeError', 'RemoteDisconnected', 'BadStatusLine', 'ValueError', 'FileNotFoundError',
          'PermissionError')
POLICY_NAMES = ('hosting-api-ingress', 'hosting-web-ingress', 'hosting-postgres-ingress',
                'anxious-lookout-node-protection', 'anxious-lookout-tenant-boundary')
ENTITIES = ('host', 'remote-node', 'world', 'cluster', 'all', 'init', 'health', 'kube-apiserver', 'ingress',
            'unmanaged')
LOCALHOST = ('always', 'policy', 'auto')
KPR = ('true', 'false', 'strict', 'partial', 'disabled')
TRAEFIK = {'k8s:io.kubernetes.pod.namespace': 'kube-system', 'k8s:app.kubernetes.io/name': 'traefik'}
RUNNER = {'k8s:io.kubernetes.pod.namespace': 'codex-runner', 'k8s:anxious.io/managed': 'user-runner'}
STATES = ('enforcing', 'non-enforcing', 'disabled')


class Refused(Exception):
    pass


def out(kind, **fields):
    print(json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


def read(path):
    """Guest file read that never blocks or follows a link (lstat type first, O_NOFOLLOW | O_NONBLOCK, fstat)."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16 << 20:
        raise OSError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_ino != info.st_ino:
            raise OSError()
        return stream.read(16 << 20)


def kjson(args):
    done = subprocess.run([K3S, 'kubectl'] + args + ['-o', 'json'], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=60, check=False)
    if done.returncode != 0:
        raise Refused('lookup')
    return json.loads(done.stdout)


def typed(value):
    if value is None:
        return 'null'
    if value is True or value is False or type(value) is int:
        return value
    return 'other'


def client():
    found = sorted(x for x in os.listdir(EVIDENCE) if RUN.fullmatch(x))
    if len(found) != 1:
        out('client', runs=len(found))
        return
    folder = os.path.join(EVIDENCE, found[0])
    value = {'run': found[0], 'hop3FileForS4a': os.path.lexists(os.path.join(folder, 'client-S4a-hop3.jsonl'))}
    try:
        raw = read(os.path.join(folder, 'client-S4a-hop2.jsonl'))
    except FileNotFoundError:
        value['hop2'] = 'missing'
        out('client', **value)
        return
    except OSError:
        value['hop2'] = 'unreadable'
        out('client', **value)
        return
    counts, ends, bad = {}, [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(item, dict):
            bad += 1
            continue
        kind = item.get('kind') if item.get('kind') in KINDS else 'other'
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'end':
            ends.append({'reason': item.get('reason') if item.get('reason') in REASONS else 'other',
                         'errorType': (item.get('errorType') if item.get('errorType') in ERRORS else 'other')
                         if 'errorType' in item else 'missing',
                         'elapsedMs': round(item['elapsedMs']) if type(item.get('elapsedMs')) in (int, float)
                         else None})
    value['hop2'] = {'counts': counts, 'unparsed': bad, 'headers': counts.get('headers', 0) > 0, 'ends': ends}
    out('client', **value)


def cilium():
    data = (kjson(['-n', 'kube-system', 'get', 'configmap', 'cilium-config']).get('data') or {})
    def pick(key, allowed):
        return 'missing' if key not in data else (data[key] if data[key] in allowed else 'other')
    out('cilium', allowLocalhost=pick('allow-localhost', LOCALHOST),
        kubeProxyReplacement=pick('kube-proxy-replacement', KPR))


def selects_api(selector, labels):
    """true / false / unknown: does this endpointSelector's matchLabels select an endpoint with these identity labels
    (`source:key=value` strings from its CiliumEndpoint)? A key without a source (or `any:`) matches any source; a key
    with `k8s:` only that source. matchExpressions, no labels to compare, or another shape → unknown."""
    if not isinstance(selector, dict) or labels is None:
        return 'unknown'
    if selector.get('matchExpressions'):
        return 'unknown'
    wanted = selector.get('matchLabels') or {}
    if not isinstance(wanted, dict):
        return 'unknown'
    for key, value in wanted.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return 'unknown'
        if key.startswith('k8s:'):
            if '%s=%s' % (key, value) not in labels:
                return False
        else:
            bare = key[4:] if key.startswith('any:') else key
            if ':' in bare.split('=')[0].split('/')[0] and not key.startswith('any:'):
                return 'unknown'  # another source prefix (reserved:, cidr:, …)
            if not any(x.split(':', 1)[-1] == '%s=%s' % (bare, value) for x in labels):
                return False
    return True


def sources(rule):
    found = []
    for peer in rule.get('fromEndpoints') or []:
        labels = (peer or {}).get('matchLabels') or {}
        found.append('endpoints:traefik' if labels == TRAEFIK else 'endpoints:user-runner' if labels == RUNNER
                     else 'endpoints:other')
    for entity in rule.get('fromEntities') or []:
        found.append('entities:%s' % (entity if entity in ENTITIES else 'other'))
    if rule.get('fromCIDR') or rule.get('fromCIDRSet'):
        found.append('cidr')
    known = {'fromEndpoints', 'fromEntities', 'fromCIDR', 'fromCIDRSet', 'toPorts'}
    if set(rule) - known:
        found.append('other')
    if not found:
        found.append('any-source')
    return sorted(set(found))


def ports(rule):
    if not rule.get('toPorts'):
        return 'any'
    found = []
    for block in rule['toPorts']:
        for port in (block or {}).get('ports') or []:
            raw = str((port or {}).get('port', ''))
            found.append(int(raw) if raw.isdigit() else 'other')
    return sorted(set(found), key=str)


def policies(labels):
    items = [(x, 'CNP') for x in kjson(['-n', 'm2-hosting', 'get', 'ciliumnetworkpolicies']).get('items', [])]
    items += [(x, 'CCNP') for x in kjson(['get', 'ciliumclusterwidenetworkpolicies']).get('items', [])]
    listed, host8080 = [], []
    for item, kind in items:
        name = (item.get('metadata') or {}).get('name')
        specs = [item.get('spec')] if item.get('spec') else []
        specs += list(item.get('specs') or [])
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            if 'endpointSelector' in spec:
                chosen = selects_api(spec.get('endpointSelector'), labels)
            else:
                chosen = False  # nodeSelector: a host policy, not the api endpoint
            rules = []
            for direction in ('ingress', 'ingressDeny'):
                for rule in spec.get(direction) or []:
                    if not isinstance(rule, dict):
                        continue
                    entry = {'direction': direction, 'from': sources(rule), 'ports': ports(rule)}
                    rules.append(entry)
                    # a rule with no peer field (toPorts only) admits every source, the host included
                    hostish = {'entities:host', 'entities:all', 'any-source'} & set(entry['from'])
                    if chosen is not False and hostish and (entry['ports'] == 'any' or 8080 in entry['ports']):
                        host8080.append({'policy': name if name in POLICY_NAMES else 'other', 'direction': direction,
                                         'selects': chosen, 'from': sorted(hostish)})
            deny = spec.get('enableDefaultDeny')
            listed.append({'kind': kind, 'name': name if name in POLICY_NAMES else 'other', 'selectsApi': chosen,
                           'nodeSelector': 'nodeSelector' in spec,
                           'enableDefaultDeny': {k: typed(deny[k]) for k in ('ingress', 'egress') if k in deny}
                           if isinstance(deny, dict) else 'missing' if deny is None else 'other',
                           'rules': rules})
    out('policies', items=sorted(listed, key=lambda x: (x['kind'], x['name'], str(x['selectsApi']))),
        count=len(listed))
    out('hostTo8080', admitted=any(x['direction'] == 'ingress' for x in host8080), rules=host8080)


def endpoint():
    """The api Pods' CiliumEndpoints. Returns the identity labels when there is exactly one (not printed), else None."""
    found, sets = [], []
    for item in kjson(['-n', 'm2-hosting', 'get', 'ciliumendpoints']).get('items', []):
        status = item.get('status') or {}
        identity = status.get('identity') or {}
        labels = identity.get('labels') or []
        if not isinstance(labels, list) or 'k8s:app=hosting-api' not in labels:
            continue
        ingress = (status.get('policy') or {}).get('ingress') or {}
        state = ingress.get('state', 'missing')
        found.append({'identity': identity.get('id') if type(identity.get('id')) is int else None,
                      'enforcing': typed(ingress['enforcing']) if 'enforcing' in ingress else 'missing',
                      'state': state if state in STATES or state == 'missing' else 'other'})
        sets.append({x for x in labels if isinstance(x, str)})
    out('endpoint', count=len(found), items=found)
    return sets[0] if len(sets) == 1 else None


def main():
    if os.geteuid() != 0:
        out('refused', reason='not root')
        return 3
    try:
        client()
    except Exception:
        out('client', error='read failed')
    try:
        cilium()
        labels = endpoint()
        policies(labels)
    except Exception:
        out('refused', reason='a cluster lookup failed')
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
