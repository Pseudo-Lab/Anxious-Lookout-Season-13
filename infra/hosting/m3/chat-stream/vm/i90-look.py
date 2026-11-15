"""#90 P0 remainder (DEV-VM.md P0; guest, as root): read-only facts for the stages that follow. Development VM only.

    python3 -I -B i90-look.py

Prints, judging nothing:
- every runner registry row joined to its binding: fence (state, phase, generation, revision, pod, active, ambiguous),
  plan.image and the plan digest (`oldPlanSha256`: ops.runner_binding_image.plan_digest, the same function as
  pa-switch.py `digest`), pending work counts and the completed proofs of the current generation;
- the totals: open stops/entries, unsettled outbox, open admissions, live sessions, running conversations;
- client sessions on the hosting database;
- the api and runner-controller Deployments: UID, replicas, and per container / init container its name and image,
  its env names, and the values of only these env names: RESEARCH_STREAM_ENABLE, CODEX_RUNNER_STREAM_TEXT,
  CODEX_RUNNER_STREAM_TRACE, CODEX_USER_RUNNER_IDLE_SECONDS, CODEX_USER_RUNNER_ALLOWLIST (owner UUIDs, as pa-switch);
- the runner Pods in codex-runner (names only);
- the containerd image rows of anxious-dev-runtime and anxious-dev-runner (reference, digest).

Every SQL statement runs inside `BEGIN READ ONLY` as the database superuser in postgres-0 (pa-switch.py `rows`,
unchanged but for the tag). Nothing is written anywhere; stdout only. rc: 0 printed; 3 a lookup failed (what printed
before it stands); 2 usage or not root.
"""
import json
import os
import subprocess
import sys

K3S = '/usr/local/bin/k3s'
SHOWN = ('RESEARCH_STREAM_ENABLE', 'CODEX_RUNNER_STREAM_TEXT', 'CODEX_RUNNER_STREAM_TRACE',
         'CODEX_USER_RUNNER_IDLE_SECONDS', 'CODEX_USER_RUNNER_ALLOWLIST')
DEPLOYMENTS = (('m2-hosting', 'api'), ('runner-control', 'runner-controller'))
REPOS = ('docker.io/library/anxious-dev-runtime', 'docker.io/library/anxious-dev-runner')


class Stop(Exception):
    pass


def run(cmd, timeout=60, stdin=None):
    """(rc, stdout) of a fixed command; 124 on timeout, 125 if it could not start (pa-switch.py `run`)."""
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def must(cmd, message, timeout=60, stdin=None):
    rc, out = run(cmd, timeout, stdin)
    if rc != 0:
        raise Stop('%s (rc %d)' % (message, rc))
    return out


def digest(plan):
    """ops.runner_binding_image.plan_digest: sha256 of the sorted, compact JSON (pa-switch.py `digest`)."""
    import hashlib
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def rows(tag, query):
    """JSON rows (one json_build_object per line) of a READ ONLY query as postgres in postgres-0."""
    text = 'BEGIN READ ONLY;\n/*i90:%s*/ %s\nCOMMIT;\n' % (tag, query)
    out = must([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U', 'postgres',
                '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
               'database lookup failed (%s)' % tag, 60, text.encode())
    try:
        parsed = [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop('database lookup unreadable (%s)' % tag) from None
    if not all(isinstance(row, dict) for row in parsed):
        raise Stop('database lookup unreadable (%s)' % tag)
    return parsed


OWNERS_SQL = ("SELECT json_build_object('owner',r.owner_id,'bindingOwner',b.owner_id,'state',r.state_id,'phase',r.phase,"
              "'generation',r.generation,'revision',r.revision,'pod',r.pod_uid,'active',r.active_requests,"
              "'ambiguous',r.ambiguous,'bindingState',b.state_id,'plan',b.plan,"
              "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
              "'entries',(SELECT count(*) FROM runner_termination.entries e WHERE e.owner_id=r.owner_id "
              "AND e.consumed_at IS NULL),"
              "'stops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
              "AND s.completed_at IS NULL),"
              "'admissions',(SELECT count(*) FROM runner_runtime.admissions a WHERE a.owner_id=r.owner_id "
              "AND a.completed_at IS NULL),"
              "'proof',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
              "AND s.state_id=r.state_id AND s.generation=r.generation AND s.completed_at IS NOT NULL "
              "AND s.proof IS NOT NULL)) FROM runner_runtime.registry r FULL JOIN runner_bindings.bindings b "
              "ON b.owner_id=r.owner_id ORDER BY 1::text;")
TOTALS_SQL = ("SELECT json_build_object('openStops',(SELECT count(*) FROM runner_termination.stops "
              "WHERE completed_at IS NULL),'openEntries',(SELECT count(*) FROM runner_termination.entries WHERE "
              "consumed_at IS NULL),'outbox',(SELECT count(*) FROM runner_runtime.outbox WHERE NOT settled),"
              "'admissions',(SELECT count(*) FROM runner_runtime.admissions WHERE completed_at IS NULL),"
              "'sessions',(SELECT count(*) FROM auth.sessions WHERE expires_at>now()),"
              "'running',(SELECT count(*) FROM research.conversations WHERE state='running'));")
CLIENTS_SQL = ("SELECT json_build_object('clients',count(*),'roles',coalesce(string_agg(DISTINCT "
               "usename::text,',' ORDER BY usename::text),'')) FROM pg_stat_activity WHERE datname='hosting' AND "
               "backend_type='client backend' AND pid<>pg_backend_pid();")


def owner_line(row):
    plan = row.get('plan')
    fence = {k: row.get(k) for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}
    pending = {k: row.get(k) for k in ('outbox', 'entries', 'stops', 'admissions')}
    return 'owner %s binding %s fence %s bindingState %s planImage %s oldPlanSha256 %s pending %s proof %s' % (
        row.get('owner'), row.get('bindingOwner'), json.dumps(fence, sort_keys=True), row.get('bindingState'),
        plan.get('image') if isinstance(plan, dict) else None, digest(plan) if isinstance(plan, dict) else None,
        json.dumps(pending, sort_keys=True), row.get('proof'))


def deployment_lines(namespace, name, d):
    lines = []
    status = d.get('status') or {}
    lines.append('deployment %s/%s uid %s replicas %s ready %s current %s' % (
        namespace, name, d['metadata']['uid'], d['spec'].get('replicas'), status.get('readyReplicas'),
        status.get('replicas')))
    pod = d['spec']['template']['spec']
    for kind, items in (('container', pod.get('containers') or []), ('initContainer', pod.get('initContainers') or [])):
        for item in items:
            env = item.get('env') or []
            names = [e.get('name') for e in env]
            shown = {e['name']: e.get('value') if 'value' in e else 'valueFrom' for e in env if e.get('name') in SHOWN}
            lines.append('  %s %s image %s envFrom %s env names %s shown %s' % (
                kind, item.get('name'), item.get('image'), bool(item.get('envFrom')), json.dumps(names),
                json.dumps(shown, sort_keys=True)))
    return lines


def main():
    if len(sys.argv) != 1:
        print('usage: i90-look.py')
        return 2
    if os.geteuid() != 0:
        print('not root')
        return 2
    try:
        found = rows('owners', OWNERS_SQL)
        print('registry/bindings rows %d' % len(found))
        for row in found:
            print(owner_line(row))
        totals = rows('totals', TOTALS_SQL)
        print('totals %s' % json.dumps(totals[0] if len(totals) == 1 else totals, sort_keys=True))
        clients = rows('clients', CLIENTS_SQL)
        print('client sessions on hosting %s' % json.dumps(clients[0] if len(clients) == 1 else clients, sort_keys=True))
        for namespace, name in DEPLOYMENTS:
            out = must([K3S, 'kubectl', '-n', namespace, 'get', 'deployment', name, '-o', 'json'],
                       '%s lookup failed' % name)
            for line in deployment_lines(namespace, name, json.loads(out)):
                print(line)
        names = must([K3S, 'kubectl', '-n', 'codex-runner', 'get', 'pods', '-o', 'name'], 'runner pod lookup failed')
        print('runner pods %s' % json.dumps(names.split()))
        listing = must([K3S, 'ctr', '-n', 'k8s.io', 'images', 'ls'], 'ctr images ls failed', 30)
        for line in listing.splitlines()[1:]:
            parts = line.split()
            if parts and parts[0].startswith(REPOS):
                print('image %s digest %s' % (parts[0], parts[2] if len(parts) > 2 else None))
    except Stop as stop:
        print('lookup failed: %s' % stop)
        return 3
    except (ValueError, KeyError, TypeError):
        print('lookup failed: unreadable output')
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
