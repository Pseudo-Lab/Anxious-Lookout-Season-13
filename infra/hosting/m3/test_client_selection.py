"""CLI substitutes and actual local CLI/template; always executed in Docker."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

HERE=Path(__file__).parent
SHA='a'*40
SECRET='CLIENT_SELECTION_UNIQUE_SENTINEL_9827'
FIELDS={'selectionSource','contextOverrideNonempty','hostOverrideNonempty','transport','status',
        'toolEnvironmentPlacement','daemonNodePlacement','correctivePlacementAuthorized',
        'runtimeSelectionAuthorized','controllerCreated','daemonQueries','kubernetesCalls','reason'}


@pytest.fixture(scope='session',autouse=True)
def mounted_cli_identity():
    actual=Path('/tools/docker')
    if actual.is_file():
        assert hashlib.sha256(actual.read_bytes()).hexdigest()=='60f19de561e759ac14391332aacf8c10f5f9a3e53b691bdf3e1af4149bdb158e'
        version=subprocess.run([str(actual),'--version'],capture_output=True,check=True).stdout
        assert version==b'Docker version 29.6.1, build 8900f1d\n'


@pytest.fixture
def setup(tmp_path):
    tools=tmp_path/'bin';tools.mkdir()
    (tools/'git').write_text('#!/bin/sh\ncase "$*" in *rev-parse*) printf "%s\\n" '+SHA+' ;; esac\n')
    (tools/'git').chmod(0o700)
    source=tmp_path/'source';(source/'infra'/'hosting').mkdir(parents=True)
    (source/'infra'/'hosting'/'m3').symlink_to(HERE,target_is_directory=True)
    env={k:v for k,v in os.environ.items() if k not in ('DOCKER_CONTEXT','DOCKER_HOST','DOCKER_CONFIG','BASH_ENV','ENV','SHELLOPTS','BASHOPTS','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')}
    env['PATH']=str(tools)+':'+env['PATH']
    env['MOCK_CALLS']=str(tmp_path/'calls')
    env['MOCK_SHOW']='chosen\n';env['MOCK_ENUM']='unix\n'
    docker=tools/'docker'
    docker.write_text('#!'+sys.executable+'\n'+'''
import json,os,sys,time
args=sys.argv[1:]
with open(os.environ['MOCK_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
assert args[:1]==['context'] and args[1] in ('show','inspect')
key='SHOW' if args[1]=='show' else 'ENUM'
if key=='ENUM':
 assert args[2]=='--format' and args[-1]==os.environ['MOCK_SHOW'].removesuffix('\\n')
 assert 'json' not in args[3] and '.Endpoints.docker.Host' in args[3]
if os.environ.get('MOCK_SLEEP_'+key):time.sleep(float(os.environ['MOCK_SLEEP_'+key]))
payload=os.environ['MOCK_'+key].encode()
if os.environ.get('MOCK_NUL_'+key):payload=b'chosen'+bytes([0])+b'UNIQUE_SENTINEL\\n'
sys.stdout.buffer.write(payload);sys.stdout.flush()
sys.stderr.write(os.environ.get('MOCK_ERROR', ''))
if os.environ.get('MOCK_SLEEP_AFTER_'+key):time.sleep(float(os.environ['MOCK_SLEEP_AFTER_'+key]))
raise SystemExit(int(os.environ.get('MOCK_EXIT_'+key,'0')))
''')
    docker.chmod(0o700)
    return source,docker,env,tmp_path


def collect(setup,changes=None,flags=()):
    source,docker,env,tmp_path=setup
    directory=tmp_path/('evidence-'+str(len(list(tmp_path.glob('evidence-*')))))
    directory.mkdir(mode=0o700)
    env=env|{'DOCKER_CONFIG':str(tmp_path/'client-config')}
    env.update(changes or {})
    start=time.monotonic()
    shell='/tools/sh' if Path('/tools/sh').is_file() else '/bin/sh'
    result=subprocess.run([shell,*flags,str(HERE/'run_client_selection.sh'),str(source),str(docker),str(directory),SHA],env=env,capture_output=True,timeout=5)
    assert time.monotonic()-start<3.5
    # Selection sentinels and raw context/config outputs must stay ephemeral.
    visible=result.stdout+result.stderr+b''.join(p.read_bytes() for p in directory.iterdir())
    assert SECRET.encode() not in visible and b'chosen' not in visible
    assert set(p.name for p in directory.iterdir())<= {'budget.json','result.json','complete.json'}
    if result.returncode:
        assert not (directory/'complete.json').exists()
        return None,directory,result
    record=json.loads((directory/'result.json').read_text())
    assert set(record)==FIELDS
    assert record['toolEnvironmentPlacement']==record['daemonNodePlacement']=='unproven'
    assert record['runtimeSelectionAuthorized'] is record['correctivePlacementAuthorized'] is record['controllerCreated'] is False
    assert record['daemonQueries']==record['kubernetesCalls']==0
    budget=json.loads((directory/'budget.json').read_text());done=json.loads((directory/'complete.json').read_text())
    assert done['deadlineCs']==budget['deadlineCs']==budget['startCs']+300
    assert budget['startCs']<=done['completedCs']<done['deadlineCs']
    return record,directory,result


@pytest.mark.parametrize('scheme',['unix','tcp','ssh','npipe'])
def test_host_override_never_calls_cli(setup,scheme):
    record,_,_=collect(setup,{'DOCKER_HOST':scheme+'://'+SECRET,'DOCKER_CONTEXT':''})
    assert record['selectionSource']=='environment_host' and record['transport']==scheme
    assert record['status']=='collected' and record['hostOverrideNonempty']
    assert not Path(setup[2]['MOCK_CALLS']).exists()


@pytest.mark.parametrize('value',['bad://'+SECRET,'unix://','tcp://','ssh://','npipe://','unix://'+SECRET+'\n','unix://'+SECRET+'\r'])
def test_invalid_host_remains_unknown(setup,value):
    record,_,_=collect(setup,{'DOCKER_HOST':value})
    assert record['status']=='unknown' and record['transport']=='unknown'
    assert not Path(setup[2]['MOCK_CALLS']).exists()


@pytest.mark.parametrize('changes,source',[
    ({},'current_context'),({'DOCKER_CONTEXT':'','DOCKER_HOST':''},'current_context'),
    ({'DOCKER_CONTEXT':SECRET},'environment_context'),
    ({'DOCKER_CONTEXT':SECRET,'DOCKER_HOST':'ssh://'+SECRET},'environment_context')])
def test_selection_precedence_and_only_selected_context(setup,changes,source):
    if changes.get('DOCKER_CONTEXT'):changes=changes|{'MOCK_SHOW':changes['DOCKER_CONTEXT']+'\n'}
    record,_,_=collect(setup,changes)
    assert record['status']=='collected' and record['selectionSource']==source and record['transport']=='unix'
    calls=[json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]
    assert len(calls)==2 and calls[0]==['context','show']
    assert calls[1][:3]==['context','inspect','--format'] and calls[1][-1]==(changes.get('DOCKER_CONTEXT') or 'chosen')


@pytest.mark.parametrize('show',['','\n','chosen','chosen\n\n','\nchosen\n','chosen\nother\n',
    ' chosen\n','chosen \n','-chosen\n','ch/osen\n','chosen\r\n',
    'x'*129+'\n','x'*500+'\n','chösën\n'])
def test_invalid_or_truncated_first_read_never_inspects(setup,show):
    record,_,_=collect(setup,{'MOCK_SHOW':show})
    assert record['status']=='unknown'
    calls=[json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]
    assert calls==[['context','show']]


@pytest.mark.parametrize('key',['SHOW','ENUM'])
def test_nul_is_rejected_before_it_can_be_erased(setup,key):
    record,_,_=collect(setup,{'MOCK_NUL_'+key:'1'})
    assert record['status']=='unknown'
    calls=[json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]
    assert len(calls)==(1 if key=='SHOW' else 2)


def test_maximum_name_length_has_valid_terminal_frame(setup):
    record,_,_=collect(setup,{'MOCK_SHOW':'x'*128+'\n'})
    assert record['status']=='collected'


@pytest.mark.parametrize('key',['SHOW','ENUM'])
@pytest.mark.parametrize('fault',['exit','timeout'])
def test_failure_after_valid_prefix_is_not_collected(setup,key,fault):
    changes={'MOCK_ERROR':SECRET}
    changes['MOCK_EXIT_'+key]='17' if fault=='exit' else '0'
    if fault=='timeout':changes['MOCK_SLEEP_AFTER_'+key]='2'
    record,_,_=collect(setup,changes)
    assert record['status']=='unknown'
    calls=[json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]
    assert len(calls)==(1 if key=='SHOW' else 2)


@pytest.mark.parametrize('output',['unknown\n','unix','unix\n\n','\nunix\n','unix\r\n','other\n',SECRET+'\n'])
def test_enum_frames_fail_closed(setup,output):
    record,_,_=collect(setup,{'MOCK_ENUM':output})
    assert record['status']=='unknown'


@pytest.mark.parametrize('hook',['BASH_ENV','ENV','xtrace','verbose','functrace','errtrace','extdebug','flag_x','flag_v'])
def test_inherited_traces_and_startup_hooks_refuse_before_selection(setup,hook):
    marker=setup[3]/'hook-ran';file=setup[3]/'hook'
    file.write_text('printf "%s\\n" '+SECRET+'\ntouch '+str(marker)+'\n')
    changes={'DOCKER_CONTEXT':SECRET,'DOCKER_HOST':'ssh://'+SECRET}
    flags=()
    if hook in ('BASH_ENV','ENV'):changes[hook]=str(file)
    elif hook in ('flag_x','flag_v'):flags=('-'+hook[-1],)
    elif hook=='extdebug':changes['BASHOPTS']='extdebug'
    else:changes['SHELLOPTS']='braceexpand:'+hook
    record,_,_=collect(setup,changes,flags)
    assert record['status']=='unknown' and record['reason']=='unsafe_launch'
    assert not marker.exists() and not Path(setup[2]['MOCK_CALLS']).exists()


def test_startup_exhaustion_never_classifies(setup):
    git=setup[1].with_name('git');git.write_text('#!/bin/sh\nsleep 4\n')
    record,directory,result=collect(setup,{'DOCKER_CONTEXT':SECRET})
    assert result.returncode!=0 and record is None
    assert not Path(setup[2]['MOCK_CALLS']).exists()


@pytest.mark.parametrize('variable',['DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'])
def test_tls_presence_refuses_without_loading_certificate_files(setup,variable):
    record,_,_=collect(setup,{variable:SECRET})
    assert record['status']=='unknown' and record['reason']=='tls_setup_outside_scope'
    assert not Path(setup[2]['MOCK_CALLS']).exists()


def test_remaining_deadline_cannot_admit_second_read(setup):
    setup[1].with_name('git').write_text('#!/bin/sh\ncase "$*" in *rev-parse*) sleep 2.4; printf "%s\\n" '+SHA+' ;; esac\n')
    record,_,_=collect(setup,{'MOCK_SLEEP_AFTER_SHOW':'2','MOCK_SHOW':SECRET+'\n'})
    assert record is None or record['status']=='unknown'
    calls=[json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]
    assert calls==[['context','show']]


@pytest.mark.parametrize('endpoint,transport',[
    ('unix:///private/'+SECRET,'unix'), ('tcp://'+SECRET+':123','tcp'),
    ('ssh://user@'+SECRET,'ssh'), ('npipe:////./pipe/'+SECRET,'npipe'),
    ('','unknown'), ('unix://','unknown'), ('tcp://','unknown'),
    ('ssh://','unknown'), ('npipe://','unknown'), (None,'unknown')])
def test_actual_cli_template_no_daemon(setup,endpoint,transport):
    actual=Path('/tools/docker')
    if not actual.is_file():pytest.skip('Mount actual Docker CLI read-only at /tools/docker')
    source,_,env,tmp_path=setup
    config=tmp_path/'actual-config';config.mkdir(mode=0o700)
    # No daemon socket is mounted; network is none. Only test-owned client config.
    local_env=env|{'DOCKER_CONFIG':str(config)}
    subprocess.run([str(actual),'context','create','selected','--docker','host=unix:///not-connected'],env=local_env,capture_output=True,check=True)
    context=next((config/'contexts'/'meta').glob('*/meta.json'))
    data=json.loads(context.read_text())
    if endpoint is None:del data['Endpoints']['docker']['Host']
    else:data['Endpoints']['docker']['Host']=endpoint
    context.write_text(json.dumps(data))
    subprocess.run([str(actual),'context','use','selected'],env=local_env,capture_output=True,check=True)
    real_setup=(source,actual,env,tmp_path)
    probe_env=local_env|{'DOCKER_CONTEXT':'selected'}
    probe=subprocess.run([str(actual),'context','show'],env=probe_env,capture_output=True,check=True)
    assert probe.stdout==b'selected\n',probe.stdout
    record,_,_=collect(real_setup,{'DOCKER_CONFIG':str(config),'DOCKER_CONTEXT':'selected'})
    assert record['selectionSource']=='environment_context' and record['transport']==transport
    assert record['status']==('collected' if transport!='unknown' else 'unknown')


def test_override_disagreement_never_inspects_a_different_context(setup):
    record,_,_=collect(setup,{'DOCKER_CONTEXT':SECRET,'DOCKER_HOST':'ssh://'+SECRET})
    assert record['status']=='unknown' and record['reason']=='selected_context_disagreement'
    assert record['selectionSource']=='unknown' and record['transport']=='unknown'
    assert [json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]==[['context','show']]


def test_default_context_with_host_is_ambiguous_even_when_names_match(setup):
    record,_,_=collect(setup,{'DOCKER_CONTEXT':'default','DOCKER_HOST':'ssh://'+SECRET,'MOCK_SHOW':'default\n'})
    assert record['status']=='unknown' and record['reason']=='ambiguous_default_context'
    assert record['selectionSource']==record['transport']=='unknown'
    assert [json.loads(l) for l in Path(setup[2]['MOCK_CALLS']).read_text().splitlines()]==[['context','show']]


def test_actual_cli_both_variables_preserves_selection_or_refuses(setup):
    actual=Path('/tools/docker')
    if not actual.is_file():pytest.skip('Mount actual Docker CLI read-only at /tools/docker')
    source,_,env,tmp_path=setup
    config=tmp_path/'actual-config';config.mkdir(mode=0o700)
    local_env=env|{'DOCKER_CONFIG':str(config)}
    subprocess.run([str(actual),'context','create','selected','--docker','host=unix:///not-connected'],env=local_env,capture_output=True,check=True)
    effective=local_env|{'DOCKER_CONTEXT':'selected','DOCKER_HOST':'ssh://'+SECRET}
    current=subprocess.run([str(actual),'context','show'],env=effective,capture_output=True,check=True).stdout
    record,_,_=collect((source,actual,env,tmp_path),{k:effective[k] for k in ('DOCKER_CONFIG','DOCKER_CONTEXT','DOCKER_HOST')})
    if current==b'selected\n':assert record['status']=='collected' and record['transport']=='unix'
    else:
        assert record['status']=='unknown' and record['transport']=='unknown'
        assert record['selectionSource']=='unknown' and record['reason']=='selected_context_disagreement'


def test_actual_cli_selected_default_and_missing_context(setup):
    actual=Path('/tools/docker')
    if not actual.is_file():pytest.skip('Mount actual Docker CLI read-only at /tools/docker')
    source,_,env,tmp_path=setup
    config=tmp_path/'actual-config';config.mkdir(mode=0o700)
    local_env=env|{'DOCKER_CONFIG':str(config)}
    subprocess.run([str(actual),'context','create','selected','--docker','host=unix:///not-connected'],env=local_env,capture_output=True,check=True)
    subprocess.run([str(actual),'context','use','selected'],env=local_env,capture_output=True,check=True)
    real=(source,actual,env,tmp_path)
    record,_,_=collect(real,{'DOCKER_CONFIG':str(config)})
    assert record['status']=='collected' and record['selectionSource']=='current_context' and record['transport']=='unix'
    record,_,_=collect(real,{'DOCKER_CONFIG':str(config),'DOCKER_CONTEXT':SECRET})
    assert record['status']=='unknown' and record['transport']=='unknown'
    record,_,_=collect(real,{'DOCKER_CONFIG':str(config),'DOCKER_CONTEXT':'default'})
    assert record['status']=='collected' and record['selectionSource']=='environment_context' and record['transport']=='unix'
    subprocess.run([str(actual),'context','use','default'],env=local_env,capture_output=True,check=True)
    record,_,_=collect(real,{'DOCKER_CONFIG':str(config)})
    assert record['status']=='collected' and record['selectionSource']=='current_context' and record['transport']=='unix'


def test_actual_cli_default_and_host_does_not_infer_provenance(setup):
    actual=Path('/tools/docker')
    if not actual.is_file():pytest.skip('Mount actual Docker CLI read-only at /tools/docker')
    source,_,env,tmp_path=setup
    record,_,_=collect((source,actual,env,tmp_path),{'DOCKER_CONTEXT':'default','DOCKER_HOST':'ssh://'+SECRET})
    assert record['status']=='unknown' and record['reason']=='ambiguous_default_context'
    assert record['selectionSource']==record['transport']=='unknown'
