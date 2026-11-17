"""Direct agent saves reuse version/graph validation and never consume human drafts."""
from .post_proposals import Propose as SavePost
from .research import fail
from .version_authors import saved


def save_post(store, body):
    from . import posts
    from .post_block_markdown import convert
    import os

    posts.ready(store)
    store.graph_lock()
    post = posts.owned(store, body.postId, active=True) if body.postId else None
    if post and post.version != body.expectedVersion:
        fail('conflict', 'The resource changed; reload before retrying', 409,
             {'reason': 'resource_version', 'current': posts.post_value(store, post)})
    if body.draftId:
        draft = posts.draft(store, body.draftId)
        # A foreign draft is rejected by posts.draft before it can appear in details.
        if draft.post_id != (post.id if post else None):
            fail('conflict', 'The draft belongs to another target', 409, {'reason': 'draft_target'})
        if draft.state != 'active' or draft.revision != body.expectedDraftRevision:
            fail('conflict', 'The draft changed; reload before retrying', 409,
                 {'reason': 'draft_revision', 'currentDraft': posts.draft_value(draft)})
    document, warnings = convert(body.contentInput.text, str(store.owner) + ':' + body.mutationId,
                                 os.environ.get('APP_BASE_PATH', ''))
    payload = {key: getattr(body, key) for key in posts.PostMetadata.model_fields}
    payload.update(content=document, conversionWarnings=warnings)
    post = posts.save_version(store, payload, post)
    return saved(posts.post_value(store, post), 'post')
