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

def test_equal_projection_reparent_reports_layout():
    from copy import deepcopy
    p=G['valid'][0]['input']['blocks'][0]
    a={**deepcopy(p),'id':'00000000-0000-0000-0000-000000000011','runs':[{'text':'one','marks':[]}]}
    b={**deepcopy(p),'id':'00000000-0000-0000-0000-000000000012','runs':[{'text':'two','marks':[]}]}
    q={'id':'00000000-0000-0000-0000-000000000013','type':'quote','blocks':[a,b]}
    base=canonicalize({'format':'post-blocks','version':1,'blocks':[q]})
    candidate=canonicalize({'format':'post-blocks','version':1,'blocks':[{**q,'blocks':[a]},b]})
    assert text_projection(base)==text_projection(candidate)
    assert any(c['candidateBlockId']==b['id'] and c['kind']=='layout' and 'parent' in c['changedProperties'] for c in format_changes(base,candidate))

def test_exact_ids_reserved_before_duplicate_fallback():
    from copy import deepcopy
    base=canonicalize(G['valid'][3]['input']); original=base['blocks'][0]
    new={**deepcopy(original),'id':'00000000-0000-0000-0000-000000000999'}
    candidate={**base,'blocks':[new,original]}
    changes=format_changes(base,candidate)
    assert any(c['candidateBlockId']==new['id'] and c['kind']=='added' for c in changes)
    assert len([c for c in changes if c['baseBlockId']==original['id']])==1
    base={**base,'blocks':[original,new]}
    other={**deepcopy(original),'id':'00000000-0000-0000-0000-000000000888'}
    assert format_changes(base,{**base,'blocks':[other]})[0]['kind']=='ambiguous'

@pytest.mark.parametrize('markdown', [
    '> quoted text', '- first\n  - nested\n- second',
    '| left | right |\n| --- | --- |\n| one | two |',
])
def test_seed_only_change_preserves_matched_parent_relationships(markdown):
    from app.post_block_markdown import convert
    base,_=convert(markdown,'base-seed')
    candidate,_=convert(markdown,'candidate-seed')
    assert base!=candidate
    changes=format_changes(base,candidate)
    # Anonymous row containers can remain ambiguous under the existing
    # text/type fallback; UUID-only parent changes must not be reported.
    assert all(c['kind']=='ambiguous' and c['changedProperties']==['structure'] for c in changes)
    if not markdown.startswith('|'): assert changes==[]

def test_fallback_child_moved_to_root_still_reports_parent():
    from app.post_block_markdown import convert
    base,_=convert('> original','base-seed')
    candidate,_=convert('original','candidate-seed')
    changes=format_changes(base,candidate)
    child=next(c for c in changes if c['candidateBlockId']==candidate['blocks'][0]['id'])
    assert child['kind']=='layout' and 'parent' in child['changedProperties']

def test_cover_counts_in_distinct_image_budget():
    import uuid
    from app.post_images import references
    blocks=[{'id':str(uuid.uuid4()),'type':'image','imageId':str(uuid.uuid4())} for _ in range(50)]
    body={'content':{'format':'post-blocks','version':1,'blocks':blocks},'coverImageId':blocks[0]['imageId']}
    assert len(references(body))==50
    body['coverImageId']=str(uuid.uuid4())
    with pytest.raises(ResearchError) as caught: references(body)
    assert caught.value.code=='content_limit_exceeded'

def test_readable_projection_declares_losses_and_uses_base_path():
    from app.post_blocks import markdown_projection, format_summary
    doc=canonicalize(G['valid'][1]['input'])
    markdown=markdown_projection(doc,'/view')
    assert '/view/api/media/post-images/' in markdown
    assert 'print("안녕")' in markdown
    assert format_summary(doc)['lossy'] is True
    assert 'color' in format_summary(doc)['omittedProperties']


def test_v11_projection_and_equal_width_normalization():
    from copy import deepcopy
    from app.post_blocks import markdown_projection, format_summary, digest
    for name in ('font-size-only','table-width-only','image-width-only'):
        case=next(c for c in G['formatDiff'] if c.get('name')==name)
        assert text_projection(case['base'])==text_projection(case['candidate'])
        assert markdown_projection(case['base'])==markdown_projection(case['candidate'])
    assert {'fontSize','columnWidths'} <= set(format_summary(G['valid'][0]['canonical'])['omittedProperties'])
    table=next(c for c in G['valid'] if c['name']=='table-widths-ratio')
    canonical=canonicalize(table['input'])
    assert digest(canonical)==digest(table['canonical'])
    assert format_changes(canonical,table['canonical'])==[]
    equal=next(c for c in G['valid'] if c['name']=='table-widths-equal')
    missing=next(c for c in G['valid'] if c['name']=='table-widths-omitted')
    assert digest(canonicalize(equal['input']))==digest(canonicalize(missing['input']))
    value=deepcopy(G['valid'][0]['canonical'])
    value['blocks'][0]['runs']=[{'text':'all','marks':[{'fontSize':36},{'color':'#abcdef'},'code','strike','underline','italic','bold']}]
    normalized=canonicalize(value)
    assert normalized['blocks'][0]['runs'][0]['marks']==['bold','italic','underline','strike','code',{'color':'#ABCDEF'},{'fontSize':36}]
