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
  no selector, or not exactly one api endpoint), and per ingress / ingressDeny rule:
  - its sources (`endpoints:traefik`, `endpoints:user-runner`, `endpoints:other`, `entities:<fixed>`, `cidr`, `other`,
    or `any-source` for a rule with no peer field) and `hostSource` (true for host / all / cluster / any-source; false
    for traefik, user-runner and the non-host entities; unknown for any other endpoint selector, CIDR, kube-apiserver
    or an unknown field);
  - its ports (`any`, or entries with port number / `named` / `other`, endPort, protocol from a fixed list; protocol
    missing = ANY) and `tcp8080` (true when TCP or ANY covers 8080, port 0 included; false for UDP / SCTP / ICMP or
    another port; unknown for a named port, an unknown protocol or field, or a toPorts block with more than `ports`);
  - `match` = hostSource AND tcp8080, tri-state;
  plus the count of parts that could not be read (`unparsed`) and of Kubernetes NetworkPolicies in m2-hosting (not
  interpreted);
- `hostTcp8080`: `allowRule` / `denyRule` = true when a rule of a policy that selects the api endpoint matches; unknown
  when any relevant rule or selection is unknown, or there are unparsed parts, Kubernetes NetworkPolicies, or not
  exactly one api endpoint (`reasons`); false only when every relevant rule was decided and none matches.
  review r0 (2026-11-15): an unresolved form is never folded into false or true.
This is an observation of the policy objects, not effective access: no deny precedence, no datapath (no cilium-dbg, no
flow log: hubble is disabled), no source identity of the real connection.
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


UNKNOWN = 'unknown'
HOST_ENTITIES = ('host', 'all', 'cluster')          # each of these includes the reserved host identity
NOT_HOST_ENTITIES = ('world', 'remote-node', 'init', 'health', 'ingress', 'unmanaged')
PROTOCOLS = ('TCP', 'UDP', 'SCTP', 'ICMP', 'ICMPV6', 'ANY')


def any3(values):
    """Tri-state OR: True if one is True, else unknown if one is unknown, else False (also for none)."""
    values = list(values)
    return True if True in values else UNKNOWN if UNKNOWN in values else False


def all3(values):
    """Tri-state AND: False if one is False, else unknown if one is unknown, else True."""
    values = list(values)
    return False if False in values else UNKNOWN if UNKNOWN in values else True


def sources(rule):
    """(tags, host): fixed source tags and whether the rule's peers include the host. The known peer fields are
    alternatives (OR): a peer form this does not interpret (another endpoint selector, CIDR, kube-apiserver) is an
    unknown alternative. Any other rule field (`fromRequires`, `fromNodes`, `icmps`, `authentication`, …) may be an
    added constraint or another alternative, so it makes the whole rule's `host` unknown, whatever the peers say
    (review r1: a known host peer must not erase it)."""
    tags, host = [], []
    for peer in rule.get('fromEndpoints') or []:
        peer = peer if isinstance(peer, dict) else {}
        labels = peer.get('matchLabels') if set(peer) == {'matchLabels'} else None
        if labels == TRAEFIK:
            tags.append('endpoints:traefik')
            host.append(False)
        elif labels == RUNNER:
            tags.append('endpoints:user-runner')
            host.append(False)
        else:
            tags.append('endpoints:other')
            host.append(UNKNOWN)
    for entity in rule.get('fromEntities') or []:
        tags.append('entities:%s' % (entity if entity in ENTITIES else 'other'))
        host.append(True if entity in HOST_ENTITIES else False if entity in NOT_HOST_ENTITIES else UNKNOWN)
    if 'fromCIDR' in rule or 'fromCIDRSet' in rule:
        tags.append('cidr')
        host.append(UNKNOWN)
    if set(rule) - {'fromEndpoints', 'fromEntities', 'fromCIDR', 'fromCIDRSet', 'toPorts'}:
        tags.append('other')
        return sorted(set(tags)), UNKNOWN
    if not tags:
        tags.append('any-source')  # no peer field: every source, the host included
        host.append(True)
    return sorted(set(tags)), any3(host)


def port_entry(entry):
    """(shown, tcp8080) for one toPorts.ports entry. Protocol missing = ANY. Port 0 = every port; endPort a range;
    a named or malformed port, an unknown protocol or field: unknown."""
    if not isinstance(entry, dict):
        return {'port': 'other', 'protocol': 'other'}, UNKNOWN
    protocol = str(entry.get('protocol', 'ANY')).upper()
    shown = {'protocol': protocol if protocol in PROTOCOLS else 'other'}
    proto = True if protocol in ('TCP', 'ANY') else False if protocol in PROTOCOLS else UNKNOWN
    raw = entry.get('port')
    raw = str(raw) if type(raw) in (str, int) else ''
    end = entry.get('endPort', None)
    if not raw.isdigit():
        shown['port'] = 'named' if raw else 'other'
        port = UNKNOWN
    else:
        number = int(raw)
        shown['port'] = number
        if 'endPort' in entry:
            shown['endPort'] = end if type(end) is int else 'other'
        if number == 0:
            port = True
        elif 'endPort' not in entry:
            port = number == 8080
        elif type(end) is int:
            port = number <= 8080 <= end
        else:
            port = UNKNOWN
    if set(entry) - {'port', 'protocol', 'endPort'}:
        shown['extra'] = True
        port = all3([port, UNKNOWN]) if port is not False else False
    return shown, all3([proto, port])


def ports(rule):
    """(shown, tcp8080): whether the rule's ports include TCP 8080. No toPorts = every port. A block with more than
    `ports` (L7 rules, listener, TLS) or without ports is unknown."""
    if not rule.get('toPorts'):
        return 'any', True
    shown, verdicts = [], []
    for block in rule['toPorts'] if isinstance(rule['toPorts'], list) else [None]:
        if not isinstance(block, dict) or set(block) - {'ports'} or not block.get('ports'):
            shown.append({'block': 'other'})
            verdicts.append(UNKNOWN)
            continue
        for entry in block['ports'] if isinstance(block['ports'], list) else [None]:
            seen, verdict = port_entry(entry)
            shown.append(seen)
            verdicts.append(verdict)
    return shown, any3(verdicts)


def policies(labels):
    """Allow / deny rules from the host to TCP 8080 on policies that select (or may select) the api endpoint, as an
    observation of the policy objects only: no deny precedence, no datapath. Anything not interpreted stays unknown."""
    items = [(x, 'CNP') for x in kjson(['-n', 'm2-hosting', 'get', 'ciliumnetworkpolicies']).get('items', [])]
    items += [(x, 'CCNP') for x in kjson(['get', 'ciliumclusterwidenetworkpolicies']).get('items', [])]
    k8s = len(kjson(['-n', 'm2-hosting', 'get', 'networkpolicies']).get('items', []))
    listed, found, unparsed = [], {'ingress': [], 'ingressDeny': []}, 0
    for item, kind in items:
        item = item if isinstance(item, dict) else {}
        name = (item.get('metadata') or {}).get('name')
        specs = [item.get('spec')] if 'spec' in item else []
        specs += list(item.get('specs') or []) if isinstance(item.get('specs'), list) else (
            [None] if 'specs' in item else [])
        for spec in specs:
            if not isinstance(spec, dict):
                unparsed += 1
                continue
            if 'endpointSelector' in spec:
                chosen = selects_api(spec.get('endpointSelector'), labels)
            elif 'nodeSelector' in spec:
                chosen = False  # a host policy, not the api endpoint
            else:
                chosen = UNKNOWN
            rules = []
            for direction in ('ingress', 'ingressDeny'):
                listed_rules = spec.get(direction) or []
                for rule in listed_rules if isinstance(listed_rules, list) else [None]:
                    if not isinstance(rule, dict):
                        unparsed += 1
                        continue
                    tags, host = sources(rule)
                    shown, tcp = ports(rule)
                    match = all3([host, tcp])
                    rules.append({'direction': direction, 'from': tags, 'ports': shown, 'hostSource': host,
                                  'tcp8080': tcp, 'match': match})
                    if chosen is not False:
                        found[direction].append(all3([chosen, match]))
            deny = spec.get('enableDefaultDeny')
            listed.append({'kind': kind, 'name': name if name in POLICY_NAMES else 'other', 'selectsApi': chosen,
                           'nodeSelector': 'nodeSelector' in spec,
                           'enableDefaultDeny': {k: typed(deny[k]) for k in ('ingress', 'egress') if k in deny}
                           if isinstance(deny, dict) else 'missing' if deny is None else 'other',
                           'rules': rules})
    out('policies', items=sorted(listed, key=lambda x: (x['kind'], x['name'], str(x['selectsApi']))),
        count=len(listed), unparsed=unparsed, k8sNetworkPolicies=k8s)
    why = []
    if labels is None:
        why.append('not exactly one api endpoint')
    if unparsed:
        why.append('unparsed policy parts')
    if k8s:
        why.append('Kubernetes NetworkPolicies not interpreted')
    allow = any3(found['ingress'])
    if allow is not True and why:
        allow = UNKNOWN
    deny = any3(found['ingressDeny'])
    if deny is not True and why:
        deny = UNKNOWN
    out('hostTcp8080', allowRule=allow, denyRule=deny, reasons=why,
        note='policy objects only: an allow rule observation, not effective access (no deny precedence, no datapath)')


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
