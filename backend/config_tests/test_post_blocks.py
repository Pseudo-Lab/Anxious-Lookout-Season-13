import json
from pathlib import Path
import pytest
from app.post_blocks import canonicalize, text_projection, image_ids, format_changes, pointer
from app.research import ResearchError
G = json.loads((Path(__file__).resolve().parents[1]/'contracts/post-blocks-v1/golden.json').read_text())

@pytest.mark.parametrize('case', G['valid'], ids=lambda c:c['name'])
def test_canonical_projection_and_images(case):
    actual=canonicalize(case['input'])
    assert actual==case['canonical']
    assert canonicalize(actual)==actual
    assert text_projection(actual)==case['textProjection']
    assert image_ids(actual)==case['imageIds']

@pytest.mark.parametrize('case', G['invalid'], ids=lambda c:c['name'])
def test_all_invalid_pointers(case):
    with pytest.raises(ResearchError) as caught: canonicalize(case['input'])
    err=caught.value
    assert (err.status,err.code,err.details['path'])==(case['status'],case['code'],case['path'])

@pytest.mark.parametrize('case', G['formatDiff'])
def test_diff(case):
    assert format_changes(case['base'],case['candidate'])==case['formatChanges']

def test_pointer_escaping_and_no_input_in_error():
    assert pointer(('a/b','~x',0))=='/a~1b/~0x/0'
    with pytest.raises(ResearchError) as caught: canonicalize({'secret':'PRIVATE'})
    assert 'PRIVATE' not in str(caught.value.__dict__)

def test_total_limits_and_merge_overflow():
    from copy import deepcopy
    value=deepcopy(G['valid'][0]['input'])
    value['blocks'][0]['runs']=[{'text':'x'*10001,'marks':[]}, {'text':'y'*10000,'marks':[]}]
    with pytest.raises(ResearchError) as caught: canonicalize(value)
    assert caught.value.code=='content_limit_exceeded'
    value['blocks'][0]['runs']=[{'text':'한'*1048576,'marks':[]}]
    with pytest.raises(ResearchError) as caught: canonicalize(value)
    assert caught.value.status==413

def test_safe_links_and_duplicate_color():
    from copy import deepcopy
    for link in ('https://user:pass@example.com', 'javascript:alert(1)', 'https://example.com\\@evil.test', 'https://example.com/\n'):
        value=deepcopy(G['valid'][4]['input']); value['blocks'][0]['runs'][0]['link']=link
        with pytest.raises(ResearchError): canonicalize(value)
    value['blocks'][0]['runs'][0].pop('link')
    value['blocks'][0]['runs'][0]['marks']=[{'color':'#abcdef'},{'color':'#ABCDEF'}]
    with pytest.raises(ResearchError): canonicalize(value)

def test_format_changes_target_move_add_remove():
    from copy import deepcopy
    a=canonicalize(G['valid'][1]['input']); b=deepcopy(a)
    b['blocks'][6]['imageId']='00000000-0000-0000-0000-000000000999'
    assert any('imageId' in x['changedProperties'] for x in format_changes(a,b))
    b['blocks'].reverse()
    assert any(x['kind']=='moved' for x in format_changes(a,b))
    b['blocks'].pop()
    assert any(x['kind']=='removed' for x in format_changes(a,b))
