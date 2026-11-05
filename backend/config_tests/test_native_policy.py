"""#68 native shell approval path and startup self-check (pure, no DB, no Codex binary).

These tests prove the runner's own decisions on given app-server messages. Whether pinned
Codex 0.160.1 really sends these messages, in this order, for every command is VM evidence.
"""
import os
import queue
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from app.codex_policy import CodexFailure
from runner import native_policy as policy
from runner.app import Native
from runner.native_policy import Decision

ROOT = '/state'
WORK = '/state/workspace'
ETC = Path(__file__).resolve().parents[1] / 'runner' / 'codex-etc'
RUNNER = Path(__file__).resolve().parents[1] / 'runner'


# --- the image's policy files ---------------------------------------------------------------

def test_image_files_are_exactly_the_checked_policy():
    with open(ETC / 'config.toml', 'rb') as file:
        assert tomllib.load(file) == policy.system_config(ROOT)
    with open(ETC / 'requirements.toml', 'rb') as file:
        assert tomllib.load(file) == policy.requirements(ROOT)


def test_profile_has_the_load_bearing_deny_entries():
    # Codex retries a sandbox-denied command unsandboxed, without approval, unless a deny entry exists.
    table = policy.filesystem(ROOT)
    denied = {path for path, access in table.items() if access == 'deny'}
    assert denied == {'/control', '/run/codex-broker', '/state/codex', '/state/home'}
    assert table[':minimal'] == 'read' and table[':workspace_roots'] == {'.': 'write'}
    assert policy.requirements(ROOT)['permissions']['filesystem']['deny_read'] == list(policy.deny_paths(ROOT))
    assert policy.requirements(ROOT)['features'] == {'unified_exec': False}


@pytest.fixture
def files(tmp_path, monkeypatch):
    config, requirements = tmp_path / 'config.toml', tmp_path / 'requirements.toml'
    config.write_bytes((ETC / 'config.toml').read_bytes())
    requirements.write_bytes((ETC / 'requirements.toml').read_bytes())
    owner = {'uid': 0, 'mode': 0o100644}
    real = os.lstat

    def lstat(path):
        value = real(path)
        return os.stat_result((owner['mode'], *value[1:4], owner['uid'], *value[5:]))
    monkeypatch.setattr(os, 'lstat', lstat)
    monkeypatch.setattr(os, 'access', lambda path, mode: False)
    pairs = ((str(config), policy.system_config), (str(requirements), policy.requirements))
    return config, requirements, owner, pairs


def test_check_files_accepts_root_owned_exact_files(files):
    *_, pairs = files
    policy.check_files(ROOT, pairs)


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'deny-removed', 'extra-key', 'not-root', 'group-writable',
                                    'other-root'])
def test_check_files_refuses_damaged_or_writable(files, damage):
    config, requirements, owner, pairs = files
    root = ROOT
    if damage == 'missing': config.unlink()
    if damage == 'corrupt': config.write_text('[permissions.native\n')
    if damage == 'deny-removed': config.write_text(config.read_text().replace('"/control" = "deny"\n', ''))
    if damage == 'extra-key': requirements.write_text(requirements.read_text() + 'allow_login_shell = true\n')
    if damage == 'not-root': owner['uid'] = 10001
    if damage == 'group-writable': owner['mode'] = 0o100664
    if damage == 'other-root': root = '/elsewhere'
    with pytest.raises(CodexFailure):
        policy.check_files(root, pairs)


# --- what the app-server reports applied ----------------------------------------------------

def good_requirements():
    return {'requirements': {'allowedApprovalPolicies': ['untrusted'], 'allowedApprovalsReviewers': ['user'],
                             'featureRequirements': {'unified_exec': False}, 'allowedSandboxModes': None}}


def test_requirements_applied():
    policy.check_requirements(good_requirements())


@pytest.mark.parametrize('damage', ['none', 'no-requirements', 'policy-never', 'policy-extra', 'reviewer-auto',
                                    'reviewer-missing', 'unified-on', 'unified-missing', 'features-missing'])
def test_requirements_not_applied_refuses(damage):
    value = good_requirements()
    requirements = value['requirements']
    if damage == 'none': value = None
    if damage == 'no-requirements': value['requirements'] = None
    if damage == 'policy-never': requirements['allowedApprovalPolicies'] = ['never']
    if damage == 'policy-extra': requirements['allowedApprovalPolicies'] = ['untrusted', 'on-request']
    if damage == 'reviewer-auto': requirements['allowedApprovalsReviewers'] = ['user', 'auto_review']
    if damage == 'reviewer-missing': del requirements['allowedApprovalsReviewers']
    if damage == 'unified-on': requirements['featureRequirements']['unified_exec'] = True
    if damage == 'unified-missing': requirements['featureRequirements'] = {}
    if damage == 'features-missing': del requirements['featureRequirements']
    with pytest.raises(CodexFailure):
        policy.check_requirements(value)


def good_config():
    system = policy.system_config(ROOT)
    return {'config': {**deepcopy(system), 'model': 'gpt-6.1-sol', 'features': {'shell_tool': True}}, 'origins': {},
            'layers': [{'name': {'type': 'system', 'file': '/etc/codex/config.toml'}, 'version': '1', 'config': deepcopy(system)},
                       {'name': {'type': 'user', 'file': '/state/codex/config.toml'}, 'version': '1', 'config': {}},
                       {'name': {'type': 'project', 'dotCodexFolder': '/state/workspace/.codex'}, 'version': '1',
                        'config': {'sandbox_mode': 'danger-full-access'}, 'disabledReason': 'untrusted project'},
                       {'name': {'type': 'sessionFlags'}, 'version': '1', 'config': {'features': {'shell_tool': True}}}]}


def test_config_applied():
    policy.check_config(good_config(), ROOT)


@pytest.mark.parametrize('damage', ['deny-removed', 'deny-added', 'workspace-wider', 'effective-overridden', 'user-layer',
                                    'session-layer', 'project-enabled', 'project-no-reason', 'system-missing',
                                    'system-twice', 'system-other-file', 'sandbox-mode', 'approval-never',
                                    'layers-missing', 'layer-malformed', 'not-dict', 'approval-in-disabled-layer',
                                    'approval-in-system-layer'])
def test_config_not_applied_refuses(damage):
    value = good_config()
    config, layers = value['config'], value['layers']
    profile = config['permissions']['native']['filesystem']
    if damage == 'deny-removed': del profile['/control']
    if damage == 'deny-added': profile['/state'] = 'deny'
    if damage == 'workspace-wider': profile['/'] = 'write'
    if damage == 'effective-overridden': config['default_permissions'] = ':workspace'
    if damage == 'user-layer': layers[1]['config'] = {'permissions': {'native': {}}}
    if damage == 'session-layer': layers[3]['config']['approvals_reviewer'] = 'auto_review'
    if damage == 'project-enabled': layers[2].pop('disabledReason')
    if damage == 'project-no-reason': layers[2]['disabledReason'] = ''
    if damage == 'system-missing': del layers[0]
    if damage == 'system-twice': layers.append(deepcopy(layers[0]))
    if damage == 'system-other-file': layers[0]['name']['file'] = '/state/workspace/config.toml'
    if damage == 'sandbox-mode': config['sandbox_mode'] = 'danger-full-access'
    if damage == 'approval-never': config['approval_policy'] = 'never'
    if damage == 'layers-missing': del value['layers']
    if damage == 'layer-malformed': layers[1] = {'name': 'user'}
    if damage == 'not-dict': value = []
    if damage == 'approval-in-disabled-layer': layers[2]['config'] = {'approval_policy': 'untrusted'}
    if damage == 'approval-in-system-layer': layers[0]['config']['approval_policy'] = 'untrusted'
    with pytest.raises(CodexFailure):
        policy.check_config(value, ROOT)


def good_thread():
    return {'thread': {'id': 't'}, 'model': 'gpt-6.1-sol', 'cwd': WORK, 'approvalPolicy': 'untrusted',
            'approvalsReviewer': 'user', 'activePermissionProfile': {'id': 'native', 'extends': None},
            'sandbox': {'type': 'workspaceWrite'}}


def test_thread_applied():
    policy.check_thread(good_thread(), Path(WORK))


@pytest.mark.parametrize('damage', ['policy', 'reviewer', 'profile', 'profile-missing', 'cwd', 'missing-field', 'error'])
def test_thread_start_or_resume_mismatch_refuses(damage):
    value = good_thread()
    if damage == 'policy': value['approvalPolicy'] = 'never'
    if damage == 'reviewer': value['approvalsReviewer'] = 'auto_review'
    if damage == 'profile': value['activePermissionProfile'] = {'id': ':workspace'}
    if damage == 'profile-missing': del value['activePermissionProfile']
    if damage == 'cwd': value['cwd'] = '/state'
    if damage == 'missing-field': del value['approvalPolicy']
    if damage == 'error': value = None
    with pytest.raises(CodexFailure):
        policy.check_thread(value, Path(WORK))


# --- the only decisions the runner can send -------------------------------------------------

def test_decisions_are_accept_and_decline_only():
    assert {decision.value for decision in Decision} == {'accept', 'decline'}
    assert policy.answer(7, Decision.ACCEPT) == {'id': 7, 'result': {'decision': 'accept'}}
    for value in ('accept', 'acceptForSession', {'acceptWithExecpolicyAmendment': {}}, None):
        with pytest.raises(TypeError):
            policy.answer(7, value)


def test_no_session_rule_or_network_decision_exists_in_runner_code():
    # Structural: no runner source can name a session-wide, rule-adding or network-amending decision.
    forbidden = ('acceptForSession', 'acceptWithExecpolicyAmendment', 'applyNetworkPolicyAmendment',
                 'execpolicy_amendment', 'network_policy_amendment')
    for source in RUNNER.rglob('*.py'):
        text = source.read_text()
        assert not [word for word in forbidden if word in text], source


# --- approval answers in the runner ---------------------------------------------------------

def command(**changes):
    value = {'kind': 'command', 'threadId': 'thread', 'turnId': 'turn', 'itemId': 'item-1', 'startedAtMs': 1,
             'approvalId': None, 'environmentId': None, 'reason': None, 'networkApprovalContext': None,
             'command': 'python -m pytest -q', 'cwd': WORK, 'commandActions': [],
             'proposedExecpolicyAmendment': ['python', '-m', 'pytest', '-q'],
             'proposedNetworkPolicyAmendments': None, 'availableDecisions': ['accept', 'decline']}
    value.update(changes)
    return {'id': 'request-' + value['itemId'], 'method': 'item/commandExecution/requestApproval', 'params': value}


def file_change(**changes):
    value = {'threadId': 'thread', 'turnId': 'turn', 'itemId': 'patch-1', 'startedAtMs': 1, 'reason': None, 'grantRoot': None}
    value.update(changes)
    return {'id': 'request-' + value['itemId'], 'method': 'item/fileChange/requestApproval', 'params': value}


@pytest.fixture
def native():
    value = Native.__new__(Native)
    value.messages, value.completed, value.next_id = queue.Queue(), {}, 7
    value.auth, value.blocked_reason, value.external = None, None, False
    value.work = Path(WORK)
    value.sent, value.authorized = [], []
    value.send = value.sent.append
    value.allow = True

    def authorize(turn, arguments):
        value.authorized.append((turn['turnId'], arguments))
        if isinstance(value.allow, Exception): raise value.allow
        return value.allow
    value.authorize_shell = authorize
    return value


@pytest.fixture
def turn():
    return {'thread': 'thread', 'turnId': 'turn', 'pendingTools': [], 'approvalItems': set(), 'session': 's',
            'request': 'r', 'token': 't' * 40, 'scope': {'page': {'kind': 'document_workspace'}}}


def decision(native, identity):
    return [reply['result']['decision'] for reply in native.sent if reply['id'] == identity]


def test_authorized_command_is_accepted_for_that_request_only(native, turn):
    native.reply_approval(command(), turn)
    assert native.sent == [{'id': 'request-item-1', 'result': {'decision': 'accept'}}]
    # Bound to the pending request: its command and cwd went to the permission check.
    assert native.authorized == [('turn', {'command': 'python -m pytest -q', 'cwd': WORK})]


def test_rule_proposal_is_ignored_not_granted(native, turn):
    # 0.160.1 attaches proposedExecpolicyAmendment to ordinary prompts; only a one-time accept goes back.
    native.reply_approval(command(), turn)
    assert native.sent[0]['result'] == {'decision': 'accept'}


@pytest.mark.parametrize('field, value', [
    ('kind', 'writeStdin'), ('kind', None), ('approvalId', 'callback'), ('networkApprovalContext', {'host': 'x'}),
    ('additionalPermissions', {'network': {'enabled': True}}), ('proposedNetworkPolicyAmendments', [{'host': 'x'}]),
    ('cwd', '/state'), ('cwd', '/state/workspace/../codex'), ('cwd', 'relative'), ('command', ''), ('command', ['ls']),
    ('threadId', 'other'), ('turnId', 'previous'), ('itemId', ''), ('unknownField', 1)])
def test_command_outside_the_allow_list_is_declined_without_permission_check(native, turn, field, value):
    native.reply_approval(command(**{field: value}), turn)
    assert native.sent[0]['result'] == {'decision': 'decline'} and native.authorized == []


def test_missing_kind_is_declined(native, turn):
    message = command(); del message['params']['kind']
    native.reply_approval(message, turn)
    assert native.sent[0]['result'] == {'decision': 'decline'}


@pytest.mark.parametrize('allow', [False, RuntimeError('callback down')])
def test_failed_or_unavailable_permission_check_declines(native, turn, allow):
    native.allow = allow
    native.reply_approval(command(), turn)
    assert native.sent[0]['result'] == {'decision': 'decline'}


def test_duplicate_item_is_declined_even_after_a_decline(native, turn):
    native.allow = False
    native.reply_approval(command(), turn)
    native.allow = True
    again = command(); again['id'] = 'request-again'
    native.reply_approval(again, turn)
    assert decision(native, 'request-item-1') == ['decline'] and decision(native, 'request-again') == ['decline']
    assert len(native.authorized) == 1


def test_late_request_for_finished_turn_is_declined(native, turn):
    native.completed[('thread', 'turn')] = {'status': 'interrupted'}
    native.reply_approval(command(), turn)
    native.reply_approval(command(itemId='item-2'), None)
    assert decision(native, 'request-item-1') == ['decline'] and decision(native, 'request-item-2') == ['decline']
    assert native.authorized == []


def test_file_change_has_its_own_rules(native, turn):
    native.reply_approval(file_change(), turn)
    assert native.sent[-1] == {'id': 'request-patch-1', 'result': {'decision': 'accept'}}
    assert native.authorized == [('turn', {})]
    for changes in ({'itemId': 'patch-2', 'grantRoot': '/state'}, {'itemId': 'patch-3', 'kind': 'command'},
                    {'itemId': 'patch-4', 'turnId': 'previous'}):
        native.reply_approval(file_change(**changes), turn)
        assert native.sent[-1]['result'] == {'decision': 'decline'}
    assert len(native.authorized) == 1


def test_requests_before_turn_start_response_wait_then_bind(native, turn):
    turn['turnId'] = None
    native.messages.put(command())
    native.messages.put(file_change())
    native.messages.put({'id': 7, 'result': {'turn': {'id': 'turn'}}})
    started = native.rpc('turn/start', {'threadId': 'thread', 'input': []}, turn)
    assert native.sent[-1]['method'] == 'turn/start' and len(turn['pendingTools']) == 2 and native.authorized == []
    turn['turnId'] = started['turn']['id']
    for message in turn.pop('pendingTools'):
        native.reply_server(message, turn)
    assert decision(native, 'request-item-1') == ['accept'] and decision(native, 'request-patch-1') == ['accept']


def test_approval_outside_any_turn_is_declined(native):
    native.messages.put(command())
    native.messages.put({'id': 7, 'result': {}})
    native.rpc('thread/read', {'threadId': 'thread'})
    assert decision(native, 'request-item-1') == ['decline'] and native.authorized == []


def test_other_server_requests_get_no_grant(native, turn):
    for method in ('item/permissions/requestApproval', 'item/tool/requestUserInput', 'mcpServer/elicitation/request'):
        native.messages.put({'id': method, 'method': method, 'params': {'threadId': 'thread', 'turnId': 'turn'}})
    native.messages.put({'id': 7, 'result': {}})
    native.rpc('thread/read', {'threadId': 'thread'}, turn)
    replies = {reply['id']: reply for reply in native.sent if 'id' in reply and 'method' not in reply}
    assert all('error' in replies[method] and 'result' not in replies[method]
               for method in ('item/permissions/requestApproval', 'item/tool/requestUserInput', 'mcpServer/elicitation/request'))
    assert native.authorized == []


# --- local policy files and launch checks ---------------------------------------------------

@pytest.fixture
def local(tmp_path):
    value = Native.__new__(Native)
    value.codex_home, value.work = tmp_path / 'state' / 'codex', tmp_path / 'state' / 'workspace'
    value.codex_home.mkdir(parents=True); value.work.mkdir()
    return value


@pytest.mark.parametrize('place', ['config', 'rules', 'workspace', 'nested-parent', 'dangling-link'])
def test_model_writable_policy_refuses(local, place):
    local.check_local()
    if place == 'config': (local.codex_home / 'config.toml').write_text('')
    if place == 'rules': (local.codex_home / 'rules').mkdir()
    if place == 'workspace': (local.work / '.codex').mkdir()
    if place == 'nested-parent': (local.work.parent / '.codex').write_text('')
    if place == 'dangling-link': (local.work / '.codex').symlink_to(local.work / 'missing')
    with pytest.raises(CodexFailure):
        local.check_local()


def test_launch_check_reads_applied_policy_and_maps_failures(local, monkeypatch):
    local.root = local.work.parent
    calls = []
    answers = {'configRequirements/read': good_requirements(), 'config/read': None}

    def rpc(method, params, turn=None):
        calls.append((method, params))
        if method == 'config/read':
            value = good_config()
            value['config']['permissions']['native']['filesystem'] = policy.filesystem(str(local.root))
            value['layers'][0]['config'] = policy.system_config(str(local.root))
            for key in policy.POLICY_KEYS: value['config'][key] = policy.system_config(str(local.root)).get(key)
            return answers['config/read'] or value
        if isinstance(answers[method], Exception): raise answers[method]
        return answers[method]
    local.rpc = rpc
    local.check_policy()
    assert calls == [('configRequirements/read', {}), ('config/read', {'includeLayers': True, 'cwd': str(local.work)})]
    answers['configRequirements/read'] = CodexFailure('unavailable')
    with pytest.raises(CodexFailure) as refused:
        local.check_policy()
    assert refused.value.reason == 'policy_refused'


# --- operators can tell which check refused (names only) ------------------------------------

def test_refusal_logs_the_check_name_only(native, turn, caplog):
    import logging
    secret = 'MARKER-' + 'x' * 30
    with caplog.at_level(logging.WARNING, logger='uvicorn.error'):
        with pytest.raises(CodexFailure):
            policy.check_thread({**good_thread(), 'approvalPolicy': secret}, Path(WORK))
    lines = [record.getMessage() for record in caplog.records]
    assert lines == ['runner_policy_check {"result": "refused", "check": "thread.approval_policy"}']
    assert secret not in caplog.text


def test_local_refusals_name_the_place(local, caplog):
    import logging
    (local.work / '.codex').mkdir()
    with caplog.at_level(logging.WARNING, logger='uvicorn.error'):
        with pytest.raises(CodexFailure):
            local.check_local()
    assert '"check": "local.workspace_dot_codex"' in caplog.text and str(local.work) not in caplog.text
