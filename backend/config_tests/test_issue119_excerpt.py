"""#119 C1/C2: list-card excerpt = whole sentences from paragraphs only; computed on read for existing releases."""
from types import SimpleNamespace

from app.post_blocks import EXCERPT_HARD, EXCERPT_SOFT, canonicalize, excerpt
from app.post_publications import public_value

ID = iter(f'00000000-0000-4000-8000-{n:012d}' for n in range(1, 10000))


def para(text):
    return {'type': 'paragraph', 'id': next(ID), 'runs': [{'text': text}]}


def heading(text):
    return {'type': 'heading', 'id': next(ID), 'level': 2, 'runs': [{'text': text}]}


def doc(*blocks):
    return canonicalize({'format': 'post-blocks', 'version': 1, 'blocks': list(blocks)})


def test_headings_code_tables_images_embeds_are_excluded():
    document = doc(
        heading('소제목은 빠진다'),
        {'type': 'code', 'id': next(ID), 'text': 'print("code")'},
        {'type': 'table', 'id': next(ID), 'rows': [{'id': next(ID), 'cells': [{'id': next(ID), 'blocks': [para('표 칸')]}]}]},
        {'type': 'image', 'id': next(ID), 'imageId': next(ID), 'alt': '그림 설명', 'caption': [{'text': '캡션'}]},
        {'type': 'embed', 'id': next(ID), 'provider': 'youtube', 'resourceId': 'abcdefghijk'},
        {'type': 'divider', 'id': next(ID)},
        para('본문 첫 문장입니다.'),
    )
    assert excerpt(document) == '본문 첫 문장입니다.'


def test_paragraphs_in_lists_quotes_and_columns_count_in_order():
    document = doc(
        {'type': 'quote', 'id': next(ID), 'blocks': [para('인용 문장.')]},
        {'type': 'list', 'id': next(ID), 'ordered': False, 'items': [{'id': next(ID), 'blocks': [para('목록 항목')]}]},
        {'type': 'columns', 'id': next(ID), 'columns': [
            {'id': next(ID), 'width': 1, 'blocks': [heading('열 제목'), para('왼쪽 열.')]},
            {'id': next(ID), 'width': 1, 'blocks': [para('오른쪽 열!')]}]},
    )
    assert excerpt(document) == '인용 문장. 목록 항목 왼쪽 열. 오른쪽 열!'


def test_whole_sentences_only_up_to_the_soft_limit():
    sentence = '가' * 60 + '.'
    document = doc(para(' '.join([sentence] * 5)))
    value = excerpt(document)
    assert value == ' '.join([sentence] * 3)  # 3*61+2 = 185 <= 200; a 4th would make 247
    assert len(value) <= EXCERPT_SOFT and value.endswith('.')


def test_paragraph_end_is_a_sentence_end_and_whitespace_collapses():
    assert excerpt(doc(para('  첫  문단\n끝 '), para('둘째 문단'))) == '첫 문단 끝 둘째 문단'


def test_long_first_sentence_is_kept_whole_up_to_the_hard_cap():
    long = ' '.join(['단어'] * 100)  # 299 code points, no sentence end
    assert EXCERPT_SOFT < len(long) <= EXCERPT_HARD
    assert excerpt(doc(para(long + '. 다음 문장.'))) == long + '.'


def test_beyond_the_hard_cap_cuts_at_whitespace_with_ellipsis():
    long = ' '.join(['단어들'] * 200)
    value = excerpt(doc(para(long)))
    assert value.endswith('…') and len(value) <= EXCERPT_HARD + 1
    assert value[:-1].split(' ') == ['단어들'] * len(value[:-1].split(' '))  # no word cut in half


def test_beyond_the_hard_cap_without_whitespace_is_a_hard_cut():
    value = excerpt(doc(para('가' * 1000)))
    assert value == '가' * EXCERPT_HARD + '…'


def test_no_paragraph_text_is_empty():
    assert excerpt(doc(heading('제목만'))) == ''
    assert excerpt(doc({'type': 'paragraph', 'id': next(ID), 'runs': []})) == ''
    assert excerpt(doc(para('   '))) == ''


def test_public_value_adds_excerpt_without_touching_the_snapshot():
    content = doc(heading('소제목'), para('요약될 첫 문장. 둘째 문장.'))
    snapshot = {'id': 'r', 'slug': 's', 'content': content, 'publishedAt': '2026-11-01T00:00:00Z'}
    before = repr(snapshot)
    value = public_value(SimpleNamespace(snapshot=snapshot), None, set())
    assert value['excerpt'] == '요약될 첫 문장. 둘째 문장.'
    assert repr(snapshot) == before and 'excerpt' not in snapshot
