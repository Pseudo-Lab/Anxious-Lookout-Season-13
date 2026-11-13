"""#68 G5 (a′): the workspace's project trust reaches Codex as one inline-table `-c` argument.

Codex 0.160.1 splits a `-c` key on every `.` and does not read quotes (`config/src/overrides.rs`), and parses the
value as `_x_ = <value>` TOML, falling back to a plain string when that fails (`utils/cli/src/config_override.rs`).
So these tests check the argument by TOML parsing (tomllib), never by imitating Codex's key split. Whether the real
Codex applies it is the image build gate (`image_check.workspace_untrusted`) and the M-a measurement; not here.
"""
import tomllib

import pytest

from app.codex_policy import CodexFailure
from runner import image_check
from runner.app import native_command, toml_basic_string

WORK = '/state/workspace'
FLAGS = [dict(file_auth=a, external=e, fixture=f) for a in (False, True) for e in (False, True) for f in (False, True)]

# The command as deployed (operator access mode: external, ephemeral credentials, code mode on) before (a′),
# copied from `b9f3d7c4` backend/runner/app.py; only its last argument changes.
BEFORE = ['/usr/local/bin/codex', 'app-server', '--listen', 'stdio://',
          '-c', 'cli_auth_credentials_store="ephemeral"', '-c', 'model="gpt-6.1-sol"', '-c', 'features.shell_tool=true',
          '-c', 'model_provider="openai"', '-c', 'features.multi_agent=false', '-c', 'features.multi_agent_v2=false',
          '-c', 'features.unbounded_connection_retries=false', '-c', 'features.apps=false', '-c', 'features.plugins=false',
          '-c', 'features.remote_plugin=false', '-c', 'features.hooks=false', '-c', 'features.code_mode_host=true',
          '-c', 'features.daemon_auto_start=false', '-c', 'features.browser_use=false', '-c', 'features.computer_use=false',
          '-c', 'features.image_generation=false', '-c', 'web_search="disabled"', '-c', 'forced_login_method="chatgpt"',
          '-c', 'model_provider="access_only_openai"', '-c', 'model_providers.access_only_openai.name="OpenAI"',
          '-c', 'model_providers.access_only_openai.base_url="https://chatgpt.com/backend-api/codex"',
          '-c', 'model_providers.access_only_openai.wire_api="responses"',
          '-c', 'model_providers.access_only_openai.requires_openai_auth=true',
          '-c', 'model_providers.access_only_openai.supports_websockets=false',
          '-c', 'model_providers.access_only_openai.request_max_retries=0',
          '-c', 'model_providers.access_only_openai.stream_max_retries=0',
          '-c', 'analytics.enabled=false', '-c', 'otel.exporter="none"', '-c', 'otel.trace_exporter="none"',
          '-c', 'otel.metrics_exporter="none"',
          '-c', 'projects."/state/workspace".trust_level="untrusted"']


def build(work=WORK, **flags):
    flags = flags or dict(file_auth=False, external=True, fixture=False)
    return native_command('/usr/local/bin/codex', '/state/home', '/state/codex', work, **flags)[0]


def projects_arguments(command):
    """Every `-c` value whose key part starts with `projects`, whatever the quoting."""
    values = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == '-c']
    return [value for value in values if value.lstrip().startswith('projects')]


def parsed(value):
    key, _, raw = value.partition('=')
    assert key == 'projects'
    return tomllib.loads('v = ' + raw)['v']


@pytest.mark.parametrize('flags', FLAGS, ids=lambda f: '-'.join(k for k, v in f.items() if v) or 'none')
def test_one_projects_argument_parses_to_the_workspace_untrusted(flags):
    command = build(**flags)
    found = projects_arguments(command)
    assert len(found) == 1
    assert parsed(found[0]) == {WORK: {'trust_level': 'untrusted'}}
    assert not [arg for arg in command if arg.startswith('projects."')]
    assert command[-2:] == ['-c', found[0]]


@pytest.mark.parametrize('work', [
    '/state/work space', '/state/work.space', '/state/a.b.c', '/state/"quoted"', "/state/it's", '/state/back\\slash',
    '/state/tab\there', '/state/new\nline', '/state/del\x7f', '/state/작업공간', '/state/eq=sign', '/state/{brace}',
    '/state/[bracket]', '/state/#hash', '/state/trailing\\'])
def test_workspace_paths_give_the_same_exact_table(work):
    found = projects_arguments(build(work))
    assert len(found) == 1
    assert parsed(found[0]) == {work: {'trust_level': 'untrusted'}}


def test_a_path_that_is_not_utf8_refuses_rather_than_passing_an_unparsable_pin():
    with pytest.raises(CodexFailure) as refused:
        build('/state/bad\udcff')
    assert refused.value.reason == 'policy_refused'


def test_toml_basic_string_escapes_only_what_toml_requires():
    assert toml_basic_string('/state/workspace') == '"/state/workspace"'
    assert toml_basic_string('a"b\\c') == '"a\\"b\\\\c"'
    assert toml_basic_string('\x00\x1f\x7f') == '"\\u0000\\u001F\\u007F"'
    assert toml_basic_string('작업') == '"작업"'


def test_the_rest_of_the_command_is_unchanged():
    command = build()
    assert len(command) == len(BEFORE)
    assert command[:-1] == BEFORE[:-1]
    assert command[-1] == 'projects={"/state/workspace"={trust_level="untrusted"}}'


@pytest.mark.parametrize('flags', FLAGS, ids=lambda f: '-'.join(k for k, v in f.items() if v) or 'none')
def test_the_argument_count_does_not_depend_on_the_path(flags):
    assert len(build(**flags)) == len(build('/state/a "b".c\\d', **flags))


def config_read(projects):
    return {'config': {'projects': projects} if projects is not None else {}}


@pytest.mark.parametrize('projects,applied', [
    ({WORK: {'trust_level': 'untrusted'}}, True),
    ({'"/state/workspace"': {'trust_level': 'untrusted'}}, False),   # the earlier quoted-key pin (M-a P0)
    (None, False),
    ({WORK: {'trust_level': 'trusted'}}, False),
    ({WORK: {}}, False),
    ({WORK: 'untrusted'}, False),
    ({WORK: {'trust_level': 'untrusted'}, '/state/other': {'trust_level': 'untrusted'}}, False),
    ('projects={...}', False),                                        # Codex's string fallback
])
def test_image_check_reads_the_applied_trust(projects, applied):
    assert image_check.workspace_untrusted(config_read(projects), WORK) is applied


def test_image_check_uses_the_runner_command():
    """The build gate starts Codex with native_command itself, so it checks the argument the runner sends."""
    import inspect
    source = inspect.getsource(image_check.codex_accepts_config)
    assert 'native_command(' in source and 'workspace_untrusted(' in source and "'config.toml'" in source
