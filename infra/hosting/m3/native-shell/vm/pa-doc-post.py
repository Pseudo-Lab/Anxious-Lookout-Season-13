"""#68 pa-doc post (design r2 §2 / §4-3; read-only). Guest, root, read from stdin, never installed:

    ssh … 'sudo -n python3 -I -B -' < pa-doc-post.py

Reads the one pa-doc run's evidence (/root/native68/evidence/pa-doc-<UTC>/run.json and exit.json: exactly one run,
exit rc 0, doc pass, documentId / versionId / publicationId recorded), then, with no session and no write:
1. the public-doc look (pa-look.py's query, READ ONLY transaction): count 1, ids = [documentId];
2. GET /api/public/documents/<documentId> by an anonymous request (Host and Accept only): 200, documentId / versionId /
   id = the recorded ones, sha256 of title and content = those of the reviewed pa-doc-content.json (its pin checked).
Prints one JSON object; rc 0 only when every check holds; 3 the evidence or the content pin refused; 4 a check failed
or a lookup failed (a failed lookup is never a pass).
"""
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys

EVIDENCE = '/root/native68/evidence'
CONTENT_PATH = '/root/native68/pa-doc-content.json'
CONTENT_SHA = 'c5e66f195ab916dacab7b3c8d739bd8d3ef6a27a693d01cfcdfa36129d3f0eac'
OWNER = '00000000-0000-4000-8000-000000005805'
K3S = '/usr/local/bin/k3s'
API_HOST, API_PORT, HOST_HEADER = '10.0.0.107', 80, '192.88.99.1'


def sha_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest() if isinstance(value, str) else None


def evidence():
    names = sorted(d for d in os.listdir(EVIDENCE) if re.fullmatch(r'pa-doc-\d{8}T\d{6}Z', d))
    if len(names) != 1:
        return None, 'expected exactly one pa-doc run, found %d' % len(names)
    try:
        run = json.load(open(os.path.join(EVIDENCE, names[0], 'run.json')))
        done = json.load(open(os.path.join(EVIDENCE, names[0], 'exit.json')))
    except (OSError, ValueError) as error:
        return None, 'evidence unreadable: %s' % type(error).__name__
    ids = {k: run.get(k) for k in ('documentId', 'versionId', 'publicationId')}
    if done.get('rc') != 0 or run.get('doc') != 'pass' or not all(isinstance(v, str) for v in ids.values()):
        return None, 'the pa-doc run is not a pass with its ids recorded'
    return dict(ids, run=names[0]), None


def look():
    query = ("SELECT json_build_object('count', count(*), 'ids', coalesce(json_agg(document_id ORDER BY document_id), "
             "'[]'::json)) FROM research.publication_heads WHERE owner_id='%s' AND kind='document' AND publication_id "
             "IS NOT NULL;" % OWNER)
    try:
        p = subprocess.run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U',
                            'postgres', '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                           input='BEGIN READ ONLY;\n%s\nCOMMIT;\n' % query, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, 'lookup failed: %s' % type(error).__name__
    rows = [l for l in p.stdout.splitlines() if l.strip() not in ('', 'BEGIN', 'COMMIT')]
    if p.returncode != 0 or len(rows) != 1:
        return None, 'lookup failed: rc %s, %d rows' % (p.returncode, len(rows))
    try:
        return json.loads(rows[0]), None
    except ValueError:
        return None, 'lookup result unreadable'


def anonymous(path):
    headers = {'Host': HOST_HEADER, 'Accept': 'application/json'}
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=60)
    try:
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        status, raw = response.status, response.read(1 << 20)
    except Exception as error:
        return None, None, 'no status: %s' % type(error).__name__
    finally:
        connection.close()
    try:
        return status, json.loads(raw), None
    except ValueError:
        return status, None, 'body unreadable'


def main():
    out = {'post': 'pa-doc'}
    try:
        data = open(CONTENT_PATH, 'rb').read()
    except OSError:
        data = b''
    if hashlib.sha256(data).hexdigest() != CONTENT_SHA:
        out.update(rc=3, error='pa-doc-content.json is not the reviewed file')
        print(json.dumps(out, sort_keys=True, ensure_ascii=False))
        return 3
    content = json.loads(data)
    ids, error = evidence()
    if error:
        out.update(rc=3, error=error)
        print(json.dumps(out, sort_keys=True, ensure_ascii=False))
        return 3
    out['evidence'] = ids
    checks = {}
    result, error = look()
    out['look'] = result if result is not None else error
    checks['look count 1, the id'] = result == {'count': 1, 'ids': [ids['documentId']]}
    status, body, error = anonymous('/api/public/documents/' + ids['documentId'])
    body = body if isinstance(body, dict) else {}
    out['public'] = {'status': status, 'error': error, 'headersSent': ['Accept', 'Host'],
                     'documentId': body.get('documentId'), 'versionId': body.get('versionId'), 'id': body.get('id'),
                     'titleSha256': sha_text(body.get('title')), 'contentSha256': sha_text(body.get('content'))}
    checks['public read 200'] = status == 200
    checks['public ids'] = (body.get('documentId'), body.get('versionId'), body.get('id')) == (
        ids['documentId'], ids['versionId'], ids['publicationId'])
    checks['public title / content'] = (sha_text(body.get('title')), sha_text(body.get('content'))) == (
        sha_text(content['title']), sha_text(content['content']))
    out['checks'] = checks
    out['rc'] = 0 if all(checks.values()) else 4
    print(json.dumps(out, sort_keys=True, ensure_ascii=False))
    return out['rc']


if __name__ == '__main__':
    sys.exit(main())
