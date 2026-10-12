import json
import pytest
from access_runtime_metadata import TOOL_IMAGE, validate_runtime_metadata


@pytest.fixture
def captured(tmp_path):
    cid='a'*64; name='numeric-controller-123-456-clock'
    descriptor={'containerId':cid,'containerName':name,'image':TOOL_IMAGE,
                'identityVerified':True,'withinBudget':True}
    own={'id':cid,'name':'/'+name,'imageId':TOOL_IMAGE,'imageRef':TOOL_IMAGE,
         'user':'0:0','runtime':'runc','isolation':'','usernsMode':'',
         'networkMode':'none','pidMode':'','readonlyRootfs':True,'privileged':False,
         'capDrop':['ALL'],'securityOpt':['no-new-privileges']}
    for key,text in [('default','"sysbox-runc"'),('names','runc\nsysbox-runc\n'),
                     ('own',f'{cid} {TOOL_IMAGE} /{name}\n'+json.dumps(own))]:
        (tmp_path/f'clock-runtime-{key}.stdout').write_text(text)
        (tmp_path/f'clock-runtime-{key}.exit').write_text('0\n')
    return descriptor,own,tmp_path


def test_effective_runtime_is_selected_field_not_default(captured):
    descriptor,own,path=captured
    result=validate_runtime_metadata(descriptor,path)
    assert result['status']=='collected'
    assert result['controller']['runtime']=='runc'
    assert result['daemon']['defaultRuntime']=='sysbox-runc'
    assert result['runtimeSelectionAuthorized'] is False


@pytest.mark.parametrize('text,accepted',[
    ('io.containerd.runc.v2\nrunc\n\n',True),
    ('io.containerd.runc.v2\nrunc',False),
    ('io.containerd.runc.v2\nrunc\n',True),
    ('io.containerd.runc.v2\nrunc\n\n\n',True),
    ('',False), ('\n',False), ('\n\n',False), (' \n\n',False),
    ('\nrunc\n',False), ('io.containerd.runc.v2\n\nrunc\n',False),
    ('runc\nrunc\n\n',False), ('io.containerd.runc.v2\n',False),
    ('runc \n\n',False), (' runc\n\n',False), ('runc/other\n\n',False),
    ('runc\r\n\r\n',False), ('runc\vother\n',False)])
def test_only_trailing_template_lf_is_normalized(captured,text,accepted):
    descriptor,own,path=captured
    (path/'clock-runtime-default.stdout').write_text('"runc"\n')
    (path/'clock-runtime-names.stdout').write_text(text)
    result=validate_runtime_metadata(descriptor,path)
    assert result['status']==('collected' if accepted else 'unknown')
    assert result['runtimeSelectionAuthorized'] is False
    if accepted:
        assert result['daemon']['runtimeNames']==['io.containerd.runc.v2','runc']
        assert result['controller']['runtime']=='runc'


def test_missing_runtime_names_file_is_unknown(captured):
    descriptor,own,path=captured
    (path/'clock-runtime-names.stdout').unlink()
    assert validate_runtime_metadata(descriptor,path)['status']=='unknown'


@pytest.mark.parametrize('fault',['missing_id','foreign_id','foreign_image','foreign_name',
    'collision','late','missing_field','extra_env','truncated','failed','oversized','symlink',
    'empty_runtime','unlisted_runtime','host_network','host_pid','privileged','writable','caps','bad_type','default_only'])
def test_unknown_never_infers_or_selects_runtime(captured,fault):
    descriptor,own,path=captured
    target=path/'clock-runtime-own.stdout'
    if fault=='missing_id':descriptor['containerId']=None
    elif fault=='foreign_id':own['id']='b'*64
    elif fault=='foreign_image':own['imageId']='sha256:'+'b'*64
    elif fault=='foreign_name':own['name']='/foreign'
    elif fault=='collision':descriptor['identityVerified']=False
    elif fault=='late':descriptor['withinBudget']=False
    elif fault=='missing_field':del own['usernsMode']
    elif fault=='extra_env':own['Env']=['SECRET=value']
    elif fault=='empty_runtime':own['runtime']=''
    elif fault=='unlisted_runtime':own['runtime']='unknown'
    elif fault=='host_network':own['networkMode']='host'
    elif fault=='host_pid':own['pidMode']='host'
    elif fault=='privileged':own['privileged']=True
    elif fault=='writable':own['readonlyRootfs']=False
    elif fault=='caps':own['capDrop']=[]
    elif fault=='bad_type':own['runtime']=False
    header=target.read_text().split('\n',1)[0]
    target.write_text(header+'\n'+json.dumps(own))
    if fault=='truncated':target.write_text(header+'\n{"id":')
    elif fault=='oversized':target.write_text('x'*65537)
    elif fault=='failed':(path/'clock-runtime-own.exit').write_text('124')
    elif fault=='default_only':target.unlink()
    elif fault=='symlink':
        outside=path/'outside';target.rename(outside);target.symlink_to(outside)
    result=validate_runtime_metadata(descriptor,path)
    assert result['status']=='unknown' and result['controller'] is None
    assert result['runtimeSelectionAuthorized'] is False
    assert 'SECRET' not in json.dumps(result)
