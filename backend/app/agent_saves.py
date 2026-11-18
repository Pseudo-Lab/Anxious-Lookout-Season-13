"""Direct agent saves reuse version/graph validation and never consume human drafts."""
from .post_proposals import Propose as SavePost
from .research import fail
from .version_authors import saved

LOSS_HINT = ('Edit with contentInput.format post-block-patch (or post-blocks) to keep formatting, '
             'or set contentInput.allowFormatLoss to true to accept the loss.')


def refuse_format_loss(base):
    """Markdown cannot carry these canonical properties; never drop them silently (#107 D3)."""
    from .post_blocks import LOSSY, markdown_losses
    losses = markdown_losses(base)
    if losses:
        found = {name for node in losses for name in node['properties']}
        fail('formatting_would_be_lost', 'Markdown cannot keep this post\'s formatting. ' + LOSS_HINT, 422,
             {'path': '/contentInput', 'code': 'formatting_would_be_lost',
              'properties': [name for name in LOSSY if name in found],
              'blocks': losses[:20], 'blockCount': len(losses), 'hint': LOSS_HINT})


def candidate(store, body, base):
    """Return (canonical document, conversion warnings) for the requested input format."""
    from .post_block_markdown import convert
    import os
    content = body.contentInput
    if content.format == 'post-blocks':
        return content.content(), []
    if content.format == 'post-block-patch':
        from .post_block_patch import apply
        return apply(base, content.raw()), []
    if base is not None and not content.allowFormatLoss:
        refuse_format_loss(base)
    return convert(content.text, str(store.owner) + ':' + body.mutationId, os.environ.get('APP_BASE_PATH', ''))


def save_post(store, body):
    from . import posts
    from .post_blocks import format_changes

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
    # Base is the head the CAS above pinned; drafts are only checked, never used as base.
    base = posts.head(store, post).payload['content'] if post else None
    document, warnings = candidate(store, body, base)
    payload = {key: getattr(body, key) for key in posts.PostMetadata.model_fields}
    payload.update(content=document, conversionWarnings=warnings)
    post = posts.save_version(store, payload, post)
    result = saved(posts.post_value(store, post), 'post')
    result['formatChanges'] = format_changes(base, document) if base is not None else None
    return result
