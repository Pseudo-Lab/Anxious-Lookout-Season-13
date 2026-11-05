"""#68 native shell policy: what Codex must have applied, and the only answers the runner gives.

Codex 0.160.1 runs native shell commands and `apply_patch` in its bubblewrap sandbox under the
named permission profile `native`. Every command and file change asks for approval first
(`approvalPolicy: untrusted`). The runner decides each request here and answers with
`Decision.ACCEPT` or `Decision.DECLINE` only.

Approval requests carry `proposedExecpolicyAmendment` on almost every prompt (Codex derives it
from the command). It takes effect only through a rule-adding decision. No such decision exists
in this module, so the field is ignored; no session-wide or network-amending decision exists either.

The profile's `deny` entries are load-bearing, not a second layer. Codex retries a
sandbox-denied command without the sandbox and without a new approval unless the filesystem
policy has a deny entry, and the same check turns `require_escalated` into an ordinary
sandboxed run (`unsandboxed_execution_allowed`). So `check_config` refuses to run without them.

CODEX_HOME (`/state/codex`) is deliberately not a deny entry: 0.160.1 always makes CODEX_HOME/tmp/arg0
readable (its execve wrapper and `codex-linux-sandbox`), refuses a managed `deny_read` on an ancestor of it,
and bubblewrap masks a denied ancestor after every read bind, so `thread/start` fails. Its protection is the
profile's read roots: `:minimal` and the workspace only, compared exactly by `check_config`, with the cwd and
workspace roots compared by `check_thread`. The remaining deny entries keep the unsandboxed retry off.
Recheck all of this before moving the Codex pin (native-shell/README.md "Codex version").
"""
import enum
from pathlib import PurePosixPath

from app.codex_policy import CodexFailure

PROFILE = 'native'
SYSTEM_CONFIG = '/etc/codex/config.toml'
REQUIREMENTS = '/etc/codex/requirements.toml'
STATE_ROOT = '/state'  # The image's /etc/codex/config.toml names paths under this root.
# Keys that change who approves, how commands are sandboxed or which profile applies. Only the
# image's system layer may set them; any other enabled layer that does refuses the start.
POLICY_KEYS = frozenset({'permissions', 'default_permissions', 'approval_policy', 'approvals_reviewer',
                         'sandbox_mode', 'sandbox_workspace_write', 'agents', 'shell_environment_policy'})
# HOME for sandboxed commands (image: empty, root-owned, 0555). See codex-etc/config.toml.
SHELL_HOME = '/usr/local/share/anxious-shell-home'


class Decision(enum.Enum):
    ACCEPT = 'accept'
    DECLINE = 'decline'


def answer(identity, decision):
    if type(decision) is not Decision:
        raise TypeError('approval decision')
    return {'id': identity, 'result': {'decision': decision.value}}


def deny_paths(root):
    """Never CODEX_HOME (root/codex) or an ancestor of it: see the module docstring."""
    root = PurePosixPath(root)
    return ('/control', '/run/codex-broker', str(root/'home'))


def codex_runtime_root(root):
    """The CODEX_HOME path Codex 0.160.1 always adds as a readable root (execve wrapper, sandbox helper)."""
    return str(PurePosixPath(root)/'codex'/'tmp'/'arg0')


def covers_codex_runtime_root(path, root):
    """Would a deny on `path` hit the runtime root: the root itself, an ancestor or something inside it."""
    path, arg0 = PurePosixPath(path), PurePosixPath(codex_runtime_root(root))
    return arg0.is_relative_to(path) or path.is_relative_to(arg0)


def filesystem(root):
    """The effective `permissions.native.filesystem` table, as config/read returns it."""
    table = {':minimal': 'read', ':workspace_roots': {'.': 'write'}}
    table.update({path: 'deny' for path in deny_paths(root)})
    return table


def system_config(root):
    """The whole system layer (`/etc/codex/config.toml`): nothing more, nothing less.

    No `approval_policy`: Codex 0.160.1 refuses `untrusted` in any config file ("no longer supported") and the
    app-server does not start. `untrusted` comes from the thread/start and thread/resume override, the untrusted
    project, and the managed requirements (`allowed_approval_policies`); check_thread confirms it per thread.
    """
    return {'default_permissions': PROFILE, 'approvals_reviewer': 'user',
            'agents': {'enabled': False},
            'shell_environment_policy': {'set': {'HOME': SHELL_HOME}},
            'permissions': {PROFILE: {'filesystem': filesystem(root)}}}


def requirements(root):
    """The whole managed requirements file (`/etc/codex/requirements.toml`)."""
    return {'allowed_approval_policies': ['untrusted'], 'allowed_approvals_reviewers': ['user'],
            'features': {'unified_exec': False, 'multi_agent_v2': False, 'view_image': False, 'goals': False},
            'permissions': {'filesystem': {'deny_read': list(deny_paths(root))}}}


def refuse(check):
    """Refuse with `policy_refused` and log which check failed: a fixed check name only.

    Never file contents, tokens, paths or other user data: operators read this line to tell
    a workspace `.codex` from a broken deployment (native-shell/README.md section 6).
    """
    import json
    import logging
    logging.getLogger('uvicorn.error').warning('runner_policy_check %s', json.dumps({'result': 'refused', 'check': check}))
    raise CodexFailure('policy_refused')


def require(condition, check):
    if not condition:
        refuse(check)


def check_files(root, files=((SYSTEM_CONFIG, system_config), (REQUIREMENTS, requirements))):
    """The image's policy files: root-owned, not writable by the runner, equal to this module.

    The expected values live in this code (the image), never in the workspace or a user setting.
    """
    import os
    import stat
    import tomllib
    for path, expected in files:
        name = 'file.' + ('requirements' if expected is requirements else 'config')
        try:
            info = os.lstat(path)
            with open(path, 'rb') as file:
                value = tomllib.load(file)
        except (OSError, ValueError):
            refuse(name + '.unreadable')
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and not os.access(path, os.W_OK), name + '.ownership')
        require(value == expected(root), name + '.content')


def check_requirements(result):
    """configRequirements/read: the image's managed requirements were loaded and parsed."""
    requirements = result.get('requirements') if isinstance(result, dict) else None
    require(isinstance(requirements, dict), 'requirements.missing')
    require(requirements.get('allowedApprovalPolicies') == ['untrusted'], 'requirements.approval_policies')
    require(requirements.get('allowedApprovalsReviewers') == ['user'], 'requirements.approvals_reviewers')
    features = requirements.get('featureRequirements')
    # 0.160.1 forces unified exec on unless managed requirements pin it; off means no write_stdin.
    require(isinstance(features, dict) and features.get('unified_exec') is False, 'requirements.unified_exec')
    # Sub-agents: with multi_agent_v2 on, Codex ignores [agents] enabled = false (config.toml).
    require(features.get('multi_agent_v2') is False, 'requirements.multi_agent_v2')
    require(features.get('view_image') is False and features.get('goals') is False, 'requirements.tools_off')


def without_nulls(value):
    """config/read fills every unset field with null (measured on 0.160.1: permissions.native gets description,
    extends, network, workspace_roots and filesystem.glob_scan_max_depth as null). Dropping only nulls keeps the
    comparison exact: any set field, expected or not, still counts."""
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    return value


def check_config(result, root):
    """config/read (cwd = workspace, layers): the profile applies as written, from the system layer only."""
    require(isinstance(result, dict) and isinstance(result.get('config'), dict) and isinstance(result.get('layers'), list),
            'config.malformed')
    expected = system_config(root)
    config = result['config']
    for key in POLICY_KEYS:
        require(without_nulls(config.get(key)) == expected.get(key), 'config.effective.' + key)
    systems = []
    for layer in result['layers']:
        require(isinstance(layer, dict) and isinstance(layer.get('name'), dict) and isinstance(layer.get('config'), dict),
                'config.layer_malformed')
        kind, disabled = layer['name'].get('type'), layer.get('disabledReason')
        # Checked before the disabled-layer skip: Codex 0.160.1 refuses this key in any config file, and no layer of
        # ours sets it. Present anywhere, even in a disabled project layer, means a policy file was written.
        require('approval_policy' not in layer['config'], 'config.approval_policy_in_a_layer')
        if kind == 'project':
            require(isinstance(disabled, str) and disabled, 'config.project_layer_enabled')  # Untrusted: never applies.
        if disabled is not None:
            continue
        if kind == 'system':
            systems.append(layer)
        else:
            require(not POLICY_KEYS & set(layer['config']), 'config.policy_outside_system_layer')
    require(len(systems) == 1 and systems[0]['name'].get('file') == SYSTEM_CONFIG, 'config.system_layer')
    require(systems[0]['config'] == expected, 'config.system_layer_content')


def check_thread(result, work):
    """thread/start and thread/resume responses: this thread runs under the profile and policy."""
    require(isinstance(result, dict), 'thread.malformed')
    profile = result.get('activePermissionProfile')
    require(result.get('approvalPolicy') == 'untrusted', 'thread.approval_policy')
    require(result.get('approvalsReviewer') == 'user', 'thread.approvals_reviewer')
    require(isinstance(profile, dict) and profile.get('id') == PROFILE, 'thread.permission_profile')
    require(result.get('cwd') == str(work), 'thread.cwd')
    # The only writable and project root is the workspace; no extra writable root, no network.
    require(result.get('runtimeWorkspaceRoots') == [str(work)], 'thread.workspace_roots')
    sandbox = result.get('sandbox')
    require(isinstance(sandbox, dict) and sandbox.get('type') == 'workspaceWrite'
            and sandbox.get('writableRoots') == [] and sandbox.get('networkAccess') is False, 'thread.sandbox')


COMMAND_FIELDS = frozenset({'kind', 'threadId', 'turnId', 'itemId', 'startedAtMs', 'approvalId', 'environmentId',
                            'reason', 'networkApprovalContext', 'command', 'cwd', 'commandActions',
                            'additionalPermissions', 'proposedExecpolicyAmendment',
                            'proposedNetworkPolicyAmendments', 'availableDecisions'})
# Present and non-null means a grant beyond the profile, a subcommand/stdin callback or network: refused.
COMMAND_REFUSED = ('approvalId', 'networkApprovalContext', 'additionalPermissions', 'proposedNetworkPolicyAmendments')
FILE_FIELDS = frozenset({'threadId', 'turnId', 'itemId', 'startedAtMs', 'reason', 'grantRoot'})


def inside(path, work):
    if not isinstance(path, str) or not path.startswith('/'):
        return False
    parts = PurePosixPath(path).parts
    return '..' not in parts and PurePosixPath(path).is_relative_to(PurePosixPath(work))


def bound(params, turn):
    """The request belongs to the pending turn and names a new item of it."""
    item = params.get('itemId')
    return (turn is not None and turn.get('turnId') and params.get('threadId') == turn['thread']
            and params.get('turnId') == turn['turnId'] and isinstance(item, str) and bool(item)
            and item not in turn.setdefault('approvalItems', set()))


def command_eligible(params, turn, work):
    """item/commandExecution/requestApproval: may this request go to the shell permission check?"""
    return (isinstance(params, dict) and set(params) <= COMMAND_FIELDS and params.get('kind') == 'command'
            and all(params.get(key) is None for key in COMMAND_REFUSED)
            and isinstance(params.get('command'), str) and bool(params['command'].strip())
            and inside(params.get('cwd'), work) and bound(params, turn))


def file_change_eligible(params, turn):
    """item/fileChange/requestApproval: no `kind`; `grantRoot` asks for extra write access."""
    return (isinstance(params, dict) and set(params) <= FILE_FIELDS and params.get('grantRoot') is None
            and bound(params, turn))
