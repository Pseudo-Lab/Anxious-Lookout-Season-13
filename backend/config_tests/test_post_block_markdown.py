from pathlib import Path
import json
import pytest
from app.post_block_markdown import convert
from app.post_blocks import image_ids, text_projection
from app.research import ResearchError
G=json.loads((Path(__file__).resolve().parents[1]/'contracts/post-blocks-v1/golden.json').read_text())

@pytest.mark.parametrize('case',G['markdown'],ids=lambda c:c['name'])
def test_markdown_contract(case):
    if case.get('status'):
        with pytest.raises(ResearchError) as error: convert(case['input'],'fixed')
        assert error.value.code==case['code']
    else:
        doc,warnings=convert(case['input'],'fixed')
        assert {k:doc['blocks'][0][k] for k in ('text','language')}==case['expectedCode']
        assert [w['code'] for w in warnings]==case['warningCodes']

def test_seeds_deterministic_and_preserve_code():
    root=Path(__file__).resolve().parents[1]/'db/legacy-posts'
    for path in root.glob('*.mdx'):
        source=path.read_text().split('---',2)[2].lstrip('\n')
        a=convert(source,path.stem); assert a==convert(source,path.stem)
        assert text_projection(a[0])
        assert convert(source,path.stem+'other')[0]['blocks'][0]['id']!=a[0]['blocks'][0]['id']

def test_structural_images_only_and_unsupported_no_flatten():
    value='00000000-0000-0000-0000-000000000001'
    doc,_=convert('![alt](/api/media/post-images/'+value+')','seed')
    assert image_ids(doc)==[value]
    for bad in ('- one\n\n  two', 'text ![x](/api/media/post-images/'+value+')', '<div>x</div>', '{danger()}', '```python\n\tbad\n```'):
        with pytest.raises(ResearchError): convert(bad,'seed')

def test_surrogate_is_safe_contract_error_and_non_bmp_is_preserved():
    with pytest.raises(ResearchError) as caught: convert(json.loads('"\\ud800"'),'seed')
    assert caught.value.code=='unsupported_content'
    assert caught.value.status==422
    assert caught.value.details['path']=='/contentInput/text'
    assert '\ud800' not in str(caught.value.__dict__)
    document,_=convert('한글 😀 𝄞','seed')
    assert text_projection(document)=='한글 😀 𝄞'
