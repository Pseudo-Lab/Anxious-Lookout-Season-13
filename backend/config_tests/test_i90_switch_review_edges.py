# #90 review b8caf3e counterexamples (review: /tmp/issue90-b8/test_switch_edges.py), added unchanged as regression
# tests; only this header is new. Needs infra/ mounted (imports test_i90_vm).
import copy,json
import pytest
from test_i90_vm import switch,expected_doc,import_setup,run_import,RUNNER_REF,RUNTIME_REF

def deployment(m,doc):
    return {'metadata':{'uid':m.API[2],'resourceVersion':'10'},'spec':{'replicas':0,'template':{'spec':{
        'containers':[{'name':'api','image':doc['deployments']['api']['images']['api'],
            'env':[{'name':m.ALLOW_ENV,'value':m.OWNER},{'name':'RESEARCH_STREAM_ENABLE','value':'false'}]}],
        'initContainers':[{'name':'runner-pool-input','image':doc['deployments']['api']['images']['runner-pool-input']}]
    }}},'status':{'replicas':0}}

@pytest.mark.parametrize('damage',['flag','init-command','volume'])
def test_state_detects_nonimage_template_drift(monkeypatch,damage):
    m=switch();doc=expected_doc(m);live=deployment(m,doc)
    pod=live['spec']['template']['spec']
    if damage=='flag':pod['containers'][0]['env'][1]['value']='true'
    elif damage=='init-command':pod['initContainers'][0]['command']=['unexpected-command']
    else:pod['volumes']=[{'name':'unexpected','emptyDir':{}}]
    monkeypatch.setattr(m,'deployment',lambda *args:(m.API[2],0,0,0,live))
    assert m.api_state(doc,8)=='other', 'unrecorded template treated as old'

def test_patch_rejects_reopened_window(monkeypatch):
    m=switch();doc=expected_doc(m);live=deployment(m,doc)
    monkeypatch.setattr(m,'deployment',lambda *args:(m.API[2],live['spec']['replicas'],0,live['status']['replicas'],copy.deepcopy(live)))
    monkeypatch.setattr(m,'change',lambda *args:None)
    changed=[]
    def request(cmd,*args):
        # Another actor scales up after the pre-read and before the JSON patch.
        live['spec']['replicas']=1;live['metadata']['resourceVersion']='11'
        ops=json.loads(cmd[-1]);candidate=copy.deepcopy(live)
        def pointer(root,path):
            parts=path.strip('/').split('/')
            for name in parts[:-1]:root=root[int(name)] if isinstance(root,list) else root[name]
            return root,parts[-1]
        for op in ops:
            root,key=pointer(candidate,op['path']);key=int(key) if isinstance(root,list) else key
            if op['op']=='test' and root[key]!=op['value']:return 1,''
            if op['op']=='replace':root[key]=op['value']
        live.clear();live.update(candidate);changed.append(True)
        return 0,''
    monkeypatch.setattr(m,'run',request)
    try:m.api_image(doc,'forward',6)
    except m.Stop:pass
    assert not changed, 'image patched after replicas changed to 1'

def test_import_second_failure_is_partial_rc5(tmp_path):
    script,state,_=import_setup(tmp_path)
    value=json.loads(state.read_text());value['fail_import']='anxious-dev-runtime-90-159e271.tar';state.write_text(json.dumps(value))
    code,out=run_import(script,state)
    assert code==5 and RUNNER_REF in json.loads(state.read_text())['refs'] and RUNTIME_REF not in json.loads(state.read_text())['refs']
