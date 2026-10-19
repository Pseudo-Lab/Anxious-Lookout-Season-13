"""Deterministic, fail-closed Markdown conversion; no HTML rendering or fetches."""
import re
from uuid import UUID, NAMESPACE_URL, uuid5
from markdown_it import MarkdownIt
from .post_block_contract import Language
from typing import get_args
from .post_blocks import canonicalize, invalid, CHILDREN
from .research import ResearchError


def convert(source, seed, base_path=''):
    root=('contentInput','text')
    if not isinstance(source,str): invalid(root,'unsupported_content')
    try: size = len(source.encode('utf-8'))
    except UnicodeError: invalid(root,'unsupported_content')
    if size>1048576: invalid(root,'payload_too_large',413)
    if len(source)>200000: invalid(root,'content_limit_exceeded')
    source=source.replace('\r\n','\n')
    if re.search(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]',source): invalid(root,'unsupported_content')
    tokens=MarkdownIt('commonmark', {'html':True,'maxNesting':20}).enable(['table','strikethrough']).parse(source)
    warnings=[]
    def bad(): invalid(root,'unsupported_content')
    def inline(tokens):
        runs=[]; marks=[]; link=None
        for token in tokens:
            kind=token.type
            if kind in ('text','code_inline','softbreak','hardbreak'):
                text='\n' if kind in ('softbreak','hardbreak') else token.content
                if kind=='text' and (re.search(r'\{[^}]*\}',text) or re.match(r'^\s*(import|export)\s',text)): bad()
                if text:
                    run={'text':text,'marks':marks+(['code'] if kind=='code_inline' else [])}
                    if link: run['link']=link
                    runs.append(run)
            elif kind in ('strong_open','em_open','s_open'): marks.append({'strong_open':'bold','em_open':'italic','s_open':'strike'}[kind])
            elif kind in ('strong_close','em_close','s_close'): marks.pop()
            elif kind=='link_open': link=token.attrGet('href')
            elif kind=='link_close': link=None
            else: bad()
        return runs
    def image(token):
        source=token.attrGet('src') or ''; prefix=base_path.rstrip('/')+'/api/media/post-images/'
        try: identity=UUID(source[len(prefix):]) if source.startswith(prefix) else None
        except ValueError: bad()
        if identity is None or source!=prefix+str(identity): bad()
        alt=''.join(t.content for t in token.children or [] if t.type in ('text','code_inline'))
        if any(t.type not in ('text','code_inline','em_open','em_close','strong_open','strong_close','s_open','s_close') for t in token.children or []): bad()
        return {'type':'image','imageId':str(identity),'alt':alt,'caption':[]}
    index=0
    def parse(end=None):
        nonlocal index
        result=[]
        while index<len(tokens):
            token=tokens[index]; index+=1; kind=token.type
            if kind==end: return result
            if kind=='paragraph_open':
                if index>=len(tokens) or tokens[index].type!='inline': bad()
                children=tokens[index].children or []; index+=1
                if index>=len(tokens) or tokens[index].type!='paragraph_close': bad()
                index+=1
                if any(t.type=='image' for t in children):
                    # Only standalone images have a canonical block equivalent.
                    if len(children)!=1 or children[0].type!='image': bad()
                    result.append(image(children[0]))
                else: result.append({'type':'paragraph','runs':inline(children)})
            elif kind=='heading_open':
                if index>=len(tokens) or tokens[index].type!='inline': bad()
                result.append({'type':'heading','level':int(token.tag[1:]),'runs':inline(tokens[index].children or [])}); index+=1
                if index>=len(tokens) or tokens[index].type!='heading_close': bad()
                index+=1
            elif kind=='blockquote_open': result.append({'type':'quote','blocks':parse('blockquote_close')})
            elif kind in ('bullet_list_open','ordered_list_open'):
                ordered=kind=='ordered_list_open'; items=[]
                close='ordered_list_close' if ordered else 'bullet_list_close'
                while index<len(tokens) and tokens[index].type=='list_item_open':
                    index+=1; items.append({'blocks':parse('list_item_close')})
                if index>=len(tokens) or tokens[index].type!=close: bad()
                index+=1
                node={'type':'list','ordered':ordered,'items':items}
                if ordered: node['start']=int(token.attrGet('start') or 1)
                result.append(node)
            elif kind in ('fence','code_block'):
                language=token.info.strip() if kind=='fence' else 'plain'
                if language not in get_args(Language):
                    language='plain'; warnings.append({'code':'code_language_downgraded'})
                result.append({'type':'code','text':token.content,'language':language})
            elif kind=='hr': result.append({'type':'divider'})
            elif kind=='table_open':
                rows=[]
                while index<len(tokens) and tokens[index].type!='table_close':
                    t=tokens[index]; index+=1
                    if t.type in ('thead_open','thead_close','tbody_open','tbody_close'): continue
                    if t.type!='tr_open': bad()
                    cells=[]; header=False
                    while index<len(tokens) and tokens[index].type!='tr_close':
                        c=tokens[index]; index+=1
                        if c.type not in ('th_open','td_open'): bad()
                        header=header or c.type=='th_open'
                        align=(c.attrGet('style') or 'text-align:left').split(':')[-1].strip()
                        if index>=len(tokens) or tokens[index].type!='inline': bad()
                        runs=inline(tokens[index].children or []); index+=1
                        if index>=len(tokens) or tokens[index].type!=c.type.replace('_open','_close'): bad()
                        index+=1; cells.append({'blocks':[{'type':'paragraph','runs':runs}],'align':align})
                    if index>=len(tokens): bad()
                    index+=1; rows.append({'header':header,'cells':cells})
                if index>=len(tokens): bad()
                index+=1; result.append({'type':'table','rows':rows})
            else: bad()
        if end: bad()
        return result
    blocks=parse()
    if not blocks: blocks=[{'type':'paragraph','runs':[]}]
    namespace=uuid5(NAMESPACE_URL,'post-blocks-v1:'+str(seed))
    def identify(node,path):
        node['id']=str(uuid5(namespace,path))
        for key in CHILDREN:
            for i,child in enumerate(node.get(key,[])): identify(child,path+'/'+key+'/'+str(i))
    for i,node in enumerate(blocks): identify(node,'/blocks/'+str(i))
    try: document=canonicalize({'format':'post-blocks','version':1,'blocks':blocks},root=('contentInput',))
    except ResearchError as exc:
        if exc.code=='invalid_block_document': invalid(root,'unsupported_content')
        raise
    return document,warnings
