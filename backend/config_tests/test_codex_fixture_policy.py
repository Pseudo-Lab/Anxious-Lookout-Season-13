"""#68: the integration fixture (`tests/fixtures/codex.mock.py`) reports what the runner's start checks accept.

The fixture stands in for pinned Codex in the DB/integration tier. If its policy constants drift from
`runner/native_policy.py`, every integration path refuses at start (review, 42a1e8b). This reads the
constants from the fixture source without running it (pure, no DB) and puts them through the real checks.
"""
import ast
from pathlib import Path, PurePosixPath

from runner import native_policy as policy

FIXTURE = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'codex.mock.py'
NAMES = ('PROFILE', 'SYSTEM', 'REQUIREMENTS', 'THREAD_POLICY')


def constants(root):
    tree = ast.parse(FIXTURE.read_text())
    values = {'ROOT': PurePosixPath(root), 'str': str}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], 'id', None) in NAMES:
            values[node.targets[0].id] = eval(compile(ast.Expression(node.value), str(FIXTURE), 'eval'),  # noqa: S307
                                              {'__builtins__': {}}, values)
    assert set(NAMES) <= set(values), 'fixture constants missing'
    return values


def test_fixture_requirements_pass_the_start_check():
    policy.check_requirements({'requirements': constants(policy.STATE_ROOT)['REQUIREMENTS']})


def test_fixture_config_read_passes_the_start_check():
    system = constants(policy.STATE_ROOT)['SYSTEM']
    assert system == policy.system_config(policy.STATE_ROOT)
    # The same shape the fixture emits for config/read.
    result = {'config': dict(system), 'origins': {}, 'layers': [
        {'name': {'type': 'system', 'file': policy.SYSTEM_CONFIG}, 'version': '1', 'config': system},
        {'name': {'type': 'sessionFlags'}, 'version': '1', 'config': {'features': {'shell_tool': True}}}]}
    policy.check_config(result, policy.STATE_ROOT)


def test_fixture_thread_responses_pass_the_thread_check():
    work = str(PurePosixPath(policy.STATE_ROOT) / 'workspace')
    thread = constants(policy.STATE_ROOT)['THREAD_POLICY']
    policy.check_thread({'thread': {'id': 't'}, 'model': 'gpt-6.1-sol', 'cwd': work,
                         'runtimeWorkspaceRoots': [work], **thread}, work)
