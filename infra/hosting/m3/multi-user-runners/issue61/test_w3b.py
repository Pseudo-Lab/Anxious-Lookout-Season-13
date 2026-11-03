"""Mock harness for the #61 W3b driver (w3b-binding.py). Docker only, never on a real node:
    docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
      python3 -I /m3/multi-user-runners/issue61/test_w3b.py
mock/fake_w3b.py stands in for k3s kubectl/crictl, docker (with the binding tool), systemctl and curl. The plans
are the two live plans of the 2026-11-03T14:53:48Z read-only snapshot (mock/w3b-plans.json), so the approved
digests in w3b-expected.json are recomputed here, independently of the driver.
"""
import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

SRC = '/m3/multi-user-runners/issue61'
INSTALL = '/root/issue61'
O48, O04 = '00000000-0000-4000-8000-000000000048', '00000000-0000-4000-8000-000000005804'
RUNNER = 'docker.io/library/anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
EXECUTOR = 'docker.io/library/anxious-dev-shell-executor@sha256:9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12'
SECRET = 'SECRETPW'
PLANS = json.load(open(SRC + '/mock/w3b-plans.json'))
EXPECTED = json.load(open(SRC + '/w3b-expected.json'))
passed = failed = 0


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('ok   ' + name)
    else:
        failed += 1
        print('FAIL ' + name + (' (' + detail[-1500:] + ')' if detail else ''))


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def base_state():
    def row(owner, state, generation, revision):
        return {'state': state, 'phase': 'idle_stopped', 'generation': generation, 'revision': revision, 'pod': None,
                'active': 0, 'ambiguous': False, 'bindingState': state, 'plan': copy.deepcopy(PLANS[owner]),
                'outbox': 0, 'entries': 0, 'stops': 0, 'admissions': 0, 'proof': 1}
    return {
        'deployments': {
            'runner-control/runner-controller': {'uid': '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'replicas': 1,
                                                 'ready': 1, 'current': 1, 'label': 'app=runner-controller'},
            'm2-hosting/api': {'uid': 'fad6799a-6df5-4086-812f-cfae6212adfa', 'replicas': 1, 'ready': 1, 'current': 1,
                               'label': 'app=hosting-api'}},
        'pods': {'app=runner-controller': ['pod/runner-controller-ddc7d7dc9-rd9lm'],
                 'app=hosting-api': ['pod/api-78b9c5c8c-n4khg']},
        'runnerPods': [], 'podIP': '10.42.0.20',
        'dsn': 'postgresql+psycopg://hosting_ops:%s@postgres.m2-hosting.svc.cluster.local:5432/hosting' % SECRET,
        'images': {RUNNER: 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad',
                   EXECUTOR: 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'},
        'dockerIds': {'anxious-dev-runtime:61-5e73bc4':
                      'sha256:eb3a18acd1b2856f6a95c7686c0c0ee4e5194b6237266b3443664f37feb3377c'},
        'modules': {'/app/ops/runner_binding_image.py': '77cfc4dad550efdce270d7f7f852f1e4730a65b3eed550d4a2ca147528273778',
                    '/app/app/runner_bindings.py': '0cc5706f8db9193d1b1ff61dac46533a62a09eb951dfff39f2d2b2d0160e5342'},
        'units': {'issue16-observer.service': 'failed', 'issue16-supply.service': 'failed'},
        'containers': [], 'health': '200', 'fail': [],
        'db': {O48: row(O48, '34823dfb-6b50-4b8e-b230-191739bdc57d', 1, 4),
               O04: row(O04, '5ff583e9-abe1-4ef8-b277-4a5afead6b80', 3, 12)}}


def setup(mutate=None, fail=(), copy_expected=True):
    for path in ('/mock', INSTALL):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, mode=0o700)
    shutil.copy(SRC + '/w3b-binding.py', INSTALL)
    if copy_expected:
        shutil.copy(SRC + '/w3b-expected.json', INSTALL)
    st = base_state()
    st['fail'] = list(fail)
    if mutate:
        mutate(st)
    json.dump(st, open('/mock/w3b.json', 'w'))


def state():
    return json.load(open('/mock/w3b.json'))


def install_fakes():
    for tool, path in (('k3s', '/usr/local/bin/k3s'), ('docker', '/usr/bin/docker'), ('systemctl', '/usr/bin/systemctl'),
                       ('curl', '/usr/bin/curl')):
        with open(path, 'w') as stream:
            stream.write('#!/bin/sh\nexec python3 -I %s/mock/fake_w3b.py %s "$@"\n' % (SRC, tool))
        os.chmod(path, 0o755)


def w3b(env=None, signal_after=None):
    environ = dict(os.environ, W3B_WAIT_SECONDS='2', W3B_TOOL_TIMEOUT='3')
    environ.update(env or {})
    proc = subprocess.Popen(['python3', '-I', INSTALL + '/w3b-binding.py'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=environ)
    if signal_after:
        word, seconds = signal_after
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:  # wait until the named phase has started
            log = open('/mock/w3b-calls.log').read() if os.path.exists('/mock/w3b-calls.log') else ''
            if word in log:
                break
            time.sleep(0.1)
        time.sleep(seconds)
        proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=300)
    return proc.returncode, out.decode()


def evidence_dirs():
    root = INSTALL + '/evidence'
    return sorted(d for d in os.listdir(root) if d.startswith('w3b-')) if os.path.isdir(root) else []


def run_json():
    dirs = evidence_dirs()
    return json.load(open(os.path.join(INSTALL, 'evidence', dirs[-1], 'run.json'))) if dirs else None


def dsn_files():
    found = []
    for base, _, files in os.walk(INSTALL + '/evidence'):
        found += [os.path.join(base, f) for f in files if f == 'admin-dsn']
    return found


def plans_now(st=None):
    st = st or state()
    return {o: digest(st['db'][o]['plan']) for o in (O48, O04)}


OLD = {O48: EXPECTED[O48]['inputs']['oldPlanSha256'], O04: EXPECTED[O04]['inputs']['oldPlanSha256']}
NEW = {O48: EXPECTED[O48]['expectedToolReport']['newPlanSha256'], O04: EXPECTED[O04]['expectedToolReport']['newPlanSha256']}


def restored(st=None):
    st = st or state()
    return all(d['replicas'] == 1 and d['ready'] == 1 for d in st['deployments'].values())


def scaled_any(st=None):
    return bool((st or state()).get('scaled'))


def applied(st=None):
    return (st or state()).get('applied', [])


def common(name, rc, out, want_rc):
    """Every run: the wanted rc, no secret in the output, no admin DSN left, no tool container left."""
    st = state()
    check(name, rc == want_rc, 'rc %s want %s; %s' % (rc, want_rc, out))
    check(name + ': no secret in the output', SECRET not in out and 'postgresql' not in out, out)
    check(name + ': no admin DSN left', not dsn_files(), str(dsn_files()))
    left = [c for c in st['containers'] if c.startswith('issue61-w3b-')]  # the run's own; a planted one is not
    if want_rc == 80:
        check(name + ': the stuck tool container is the one reported', len(left) == 1, str(st['containers']))
    else:
        check(name + ': no tool container left', not left, str(st['containers']))
    return st


def main():
    install_fakes()

    # The fixture and the approved file agree (independent of the driver) -------------------------------------
    with open(SRC + '/w3b-expected.json', 'rb') as stream:
        check('expected file is the approved one (cc1049a7…)',
              hashlib.sha256(stream.read()).hexdigest() == 'cc1049a79b2e9deca166c7314b30c00c067f383609e07172b061a9881b51aa7d')
    check('snapshot plans hash to the approved old digests', {o: digest(PLANS[o]) for o in PLANS} == OLD)
    for owner in (O48, O04):
        moved = dict(PLANS[owner], image=RUNNER)
        moved['executor'] = dict(PLANS[owner]['executor'], image=EXECUTOR)
        check('…%s moved plan hashes to the approved new digest' % owner[-4:], digest(moved) == NEW[owner])

    # Happy path ---------------------------------------------------------------------------------------------
    setup()
    rc, out = w3b()
    st = common('happy path', rc, out, 0)
    check('happy: both plans moved to the approved new digests', plans_now(st) == NEW, json.dumps(plans_now(st)))
    check('happy: apply order …0048 then …5804, once each', applied(st) == [O48, O04], str(applied(st)))
    check('happy: Deployments restored (1/1, same UIDs)', restored(st))
    check('happy: controller scaled first, api restored first',
          st['scaled'] == ['runner-controller=0', 'api=0', 'api=1', 'runner-controller=1'], str(st['scaled']))
    check('happy: four tool containers (2 reports, 2 applies) plus the module check, all by name',
          [r.split('-')[2] for r in st['runs']] == ['modules', 'report', 'report', 'apply', 'apply'], str(st['runs']))
    rj = run_json()
    check('happy: evidence records both reports, confirmations and rc 0',
          rj and rj['result'] == 0 and sorted(rj['reports']) == [O48, O04]
          and all(rj['outcome'][o]['confirmed'] for o in (O48, O04)), json.dumps(rj))
    check('happy: every SQL ran READ ONLY', all(q.startswith('BEGIN READ ONLY;') for q in st['queries']))
    check('happy: prints the window proof and both report matches',
          '0 client sessions on hosting' in out and out.count('equals the approved report') == 2, out)
    ev = os.path.join(INSTALL, 'evidence', evidence_dirs()[0])
    modes = {p: oct(os.stat(os.path.join(ev, p)).st_mode & 0o777) for p in ('.', 'private', 'report-0048', 'report-5804')}
    check('happy: evidence directories are 0700', set(modes.values()) == {'0o700'}, str(modes))
    check('happy: before-plans.json holds both old plans',
          json.load(open(os.path.join(ev, 'before-plans.json'))) == PLANS)
    rc, out = w3b()
    check('rerun after a run refuses at precheck (evidence exists), nothing touched',
          rc == 3 and 'never rerun' in out and applied() == [O48, O04] and len(state()['scaled']) == 4, out)

    # Precheck refusals: rc 3, nothing scaled, no evidence -----------------------------------------------------
    def pre(name, mutate=None, fail=(), copy_expected=True, extra=None):
        setup(mutate, fail, copy_expected)
        if extra:
            extra()
        rc, out = w3b()
        st = common('precheck: ' + name, rc, out, 3)
        check('precheck: %s: nothing scaled, no tool run, no evidence' % name,
              not scaled_any(st) and not [r for r in st.get('runs', []) if '-modules-' not in r] and not evidence_dirs(),
              out)

    def tamper():
        with open(INSTALL + '/w3b-expected.json', 'a') as stream:
            stream.write(' ')
    pre('expected file missing', copy_expected=False)
    pre('expected file changed by one byte', extra=tamper)
    pre('host unit active', lambda s: s['units'].update({'issue16-controller.timer': 'active'}))
    pre('restore timer active', lambda s: s['units'].update({'issue16-restore.timer': 'active'}))
    pre('guest container exists', lambda s: s['containers'].append('someone'))
    pre('docker ps fails', fail=['ps-fail'])
    pre('tool image is another image', lambda s: s['dockerIds'].update({'anxious-dev-runtime:61-5e73bc4': 'sha256:' + '9' * 64}))
    pre('tool module differs', lambda s: s['modules'].update({'/app/app/runner_bindings.py': '0' * 64}))
    pre('runner target not resolving', lambda s: s['images'].pop(RUNNER))
    pre('executor target resolves to another ID', lambda s: s['images'].update({EXECUTOR: 'sha256:' + '1' * 64}))
    pre('api is another Deployment (UID)', lambda s: s['deployments']['m2-hosting/api'].update({'uid': 'other'}))
    pre('controller not ready', lambda s: s['deployments']['runner-control/runner-controller'].update({'ready': 0}))
    pre('a runner Pod exists', lambda s: s['runnerPods'].append('pod/runner-x'))
    for field, value in (('revision', 13), ('generation', 4), ('phase', 'ready'), ('pod', 'p'), ('active', 1),
                         ('ambiguous', True), ('state', 'other'), ('bindingState', 'other'), ('outbox', 1),
                         ('entries', 1), ('stops', 1), ('admissions', 1), ('proof', 0)):
        pre('…5804 %s=%s' % (field, value), lambda s, f=field, v=value: s['db'][O04].update({f: v}))
    pre('…0048 plan wallSeconds changed', lambda s: s['db'][O48]['plan']['executor'].update({'wallSeconds': 31}))
    pre('database lookup fails', fail=['db-fail-state'])
    pre('one owner row missing', lambda s: s['db'].pop(O04))

    # Window not proved: rc 4, no tool run, both plans old, restored -------------------------------------------
    def win(name, mutate=None, fail=()):
        setup(mutate, fail)
        rc, out = w3b()
        st = common('window: ' + name, rc, out, 4)
        check('window: %s: no report/apply ran, plans old, restored' % name,
              not [r for r in st['runs'] if '-modules-' not in r] and plans_now(st) == OLD and restored(st), out)
        return out
    win('controller Pods linger', fail=['linger-runner-controller'])
    win('api Pods linger', fail=['linger-api'])
    win('api scale patch fails after the controller was scaled', fail=['scale0-api'])
    out = win('a session stays on hosting', lambda s: s.update(extraClients=1, extraRoles=['r4_supply']))
    check('window: the remaining role is named', 'r4_supply' in out, out)
    win('fence changes inside the window', lambda s: s.update({'onScale0-api': [{'owner': O48, 'field': 'revision',
                                                                                 'value': 5}]}))
    win('admin DSN invalid', fail=['dsn-bad'])
    win('session lookup fails inside the window', lambda s: s.update({'onScale0-api': [{'fail': 'db-fail-clients'}]}))

    # Reports: rc 5, no apply ----------------------------------------------------------------------------------
    for name, fail in (('…0048 report refused', ['report-fail-0048']), ('…5804 report refused', ['report-fail-5804']),
                       ('…5804 report differs', ['report-diff-5804'])):
        setup(fail=fail)
        rc, out = w3b()
        st = common('report: ' + name, rc, out, 5)
        check('report: %s: no apply, plans old, restored' % name,
              not applied(st) and plans_now(st) == OLD and restored(st) and 'no apply ran' in out, out)
    check('report: a difference names the field', 'newPlanSha256' in out, out)

    # Apply outcomes -------------------------------------------------------------------------------------------
    setup(fail=['apply-fail-0048'])
    rc, out = w3b()
    st = common('apply: …0048 refused', rc, out, 6)
    check('apply: …0048 refused: …5804 never attempted, plans old, restored',
          not applied(st) and plans_now(st) == OLD and restored(st) and '-apply-5804-' not in ' '.join(st['runs']), out)

    setup(fail=['apply-fail-5804'])
    rc, out = w3b()
    st = common('apply: …5804 refused after …0048 moved', rc, out, 7)
    check('apply: partial is reported per owner, restored',
          plans_now(st) == {O48: NEW[O48], O04: OLD[O04]} and restored(st) and 'partial' in out, out)
    rj = run_json()
    check('apply: partial evidence (…0048 confirmed, …5804 not)',
          rj['outcome'][O48]['confirmed'] and not rj['outcome'][O04]['confirmed'] and rj['result'] == 7, json.dumps(rj))

    setup(fail=['apply-lost-0048'])
    rc, out = w3b()
    st = common('apply: …0048 committed but its answer was lost', rc, out, 7)
    check('apply: an unconfirmed commit never leads to the next owner',
          applied(st) == [O48] and plans_now(st)[O04] == OLD[O04], out)

    setup(fail=['apply-lost-5804'])
    rc, out = w3b()
    st = common('apply: …5804 committed but unconfirmed', rc, out, 9)
    check('apply: both moved, rc 9 says unconfirmed', plans_now(st) == NEW and 'not confirmed' in out, out)

    setup(fail=['apply-timeout-0048'])
    rc, out = w3b()
    st = common('apply: …0048 CLI timeout after commit (container stopped by the driver)', rc, out, 7)
    check('apply: after a timeout the container is proved gone before restore', restored(st), out)

    setup(lambda s: s.update({'afterApply-0048': {'extraClients': 1, 'extraRoles': ['anxious_api']}}))
    rc, out = w3b()
    st = common('apply: a session appears between the two applies', rc, out, 7)
    check('apply: …5804 not attempted once a session appeared; the STOPPED line names it',
          applied(st) == [O48] and 'client session(s) on hosting appeared before …5804' in out, out)

    # Container not proved stopped: rc 80, Deployments left at 0, DSN removed -------------------------------
    setup(fail=['apply-timeout-0048', 'stuck-apply-0048'])
    rc, out = w3b()
    st = common('apply: tool container stuck', rc, out, 80)
    check('apply: stuck container leaves both Deployments at 0 and says MANUAL RECOVERY',
          all(d['replicas'] == 0 for d in st['deployments'].values()) and 'MANUAL RECOVERY NEEDED' in out, out)

    setup(fail=['stuck-report-0048'])
    rc, out = w3b()
    st = common('report: container stuck', rc, out, 80)
    check('report: stuck report container: no apply, left at 0', not applied(st)
          and all(d['replicas'] == 0 for d in st['deployments'].values()), out)

    # Restore failures ---------------------------------------------------------------------------------------
    setup(fail=['rollout-api'])
    rc, out = w3b()
    st = common('restore: api rollout fails after a full move', rc, out, 70)
    check('restore: rc 70 names the failed item, bindings moved', 'RESTORE FAILED: rollout api' in out
          and plans_now(st) == NEW, out)
    setup(fail=['apply-fail-5804', 'notready-runner-controller'])
    rc, out = w3b()
    st = common('restore: failure keeps the main code (partial 7)', rc, out, 7)
    check('restore: readiness failure printed with the main code kept', 'RESTORE FAILED: readiness runner-controller' in out, out)
    setup(lambda s: s.update(health='503'))
    rc, out = w3b()
    common('restore: health not 200', rc, out, 70)

    # Interrupts ---------------------------------------------------------------------------------------------
    setup(fail=['apply-slow-0048'])
    rc, out = w3b(signal_after=('-apply-0048-', 1.5))
    st = common('interrupt during …0048 apply (after its commit)', rc, out, 7)
    check('interrupt: settled from the plan digests, restored, …5804 not attempted',
          plans_now(st) == {O48: NEW[O48], O04: OLD[O04]} and restored(st) and 'interrupted during apply' in out, out)
    setup(fail=['linger-api'])
    rc, out = w3b(env={'W3B_WAIT_SECONDS': '20'}, signal_after=('patch deployment api', 1))
    st = common('interrupt in the window', rc, out, 130)
    check('interrupt in the window: no tool run, plans old, restore attempted',
          not applied(st) and plans_now(st) == OLD and 'api=1' in st['scaled'], out)

    print('passed %d, failed %d' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
