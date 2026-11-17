---
name: anxious-research-pat
description: Read public Anxious Lookout content and read or save the PAT owner's materials, research documents, and posts from an external agent using the public HTTPS research API.
---

Use this external client when the user provides their platform HTTPS origin and
private PAT-file location. For platform-internal Codex, use its installed
`anxious-research` turn-grant skill instead. Never borrow internal grants, runner,
broker or provider credentials. This skill does not authorize publishing or
withdrawal, token issuance/revocation, or model/provider execution.

Download `scripts/pat-client.py` from the same platform origin's
`/api/research/client/pat-client.py`. It requires Python3 on POSIX with platform
OpenSSL CA paths. The user stores the token alone in their own0600 regular file
inside their own0700 directory; no symlink/hardlink. Keep the actual value out of
argv, environment, prompts, output and research content. The client accepts its
file path only. Obtain a new token through the account UI if access expired or
was revoked; do not issue or silently replace one for the user.

Set the origin to the site's public HTTPS origin, including an IP literal when
that is the site's origin, and add `--base-path` only if the site actually uses
one. Never use a Kubernetes Service or internal callback URL. No proxy, TLS
bypass, redirect following or automatic retry is supported.

Discover fields from the server schema rather than guessing:

```sh
python3 -I scripts/pat-client.py --origin https://platform.example schema research_material_save
printf '%s' '{"type":"document","limit":30}' | python3 -I scripts/pat-client.py --origin https://platform.example --credential-file /private/research.pat research_list
python3 -I scripts/pat-client.py --origin https://platform.example public --path /api/public/documents
```

Public reads need no token. An approved commenter PAT still permits only public
content; selected write does not grant editor rights. Private operations require
current approved editor/admin and PAT read/write scope; write includes read.
Read the current resource/history, source documents/materials and human post
draft before edits. Collect materials with sourceUrl, actual collectedAt and
contentKind(full/excerpt/summary); preserve citations and distinguish synthesis.
Save posts with contentInput {format: markdown, text: ...}. Existing resources
need their current expectedVersion; draftId/expectedDraftRevision are paired.
Saves append immutable versions immediately and retain active human drafts and
public snapshots. Archive only when requested, with history preserved.

Use a fresh UUID mutationId per distinct change. Only an explicitly chosen
identical retry reuses it. On CAS conflict reread/reconcile and use a fresh ID for
changed arguments; do not merely change expectedVersion on stale text. On timeout
or lost response, check versions before deciding whether to repeat. On429 or
permission failure stop and explain; never automatically retry. Report the actual
returned resource/version/destination. PAT version authors retain their immutable
credential name after revoke. A save is not publication.
