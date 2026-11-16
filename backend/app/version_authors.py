"""Version provenance is server authority, separate from the resource's owner."""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class VersionAuthor:
    conversation_id: UUID | None = None

    def columns(self):
        return {'author_kind': 'codex' if self.conversation_id else 'human',
                'author_conversation_id': self.conversation_id, 'author_credential_id': None}


def value(row):
    conversation = getattr(row, 'author_conversation_id', None)
    return {'kind': getattr(row, 'author_kind', None) or 'human',
            'conversationId': str(conversation) if conversation else None}


def saved(resource, kind):
    version = resource['latestVersion']
    return {**resource, 'saved': {
        'resourceType': kind, 'id': resource['id'],
        'title': version['title'] if kind == 'post' else resource['title'],
        'versionId': version['id'], 'versionNumber': version['number'],
        'destination': {'path': '/write/' if kind == 'post' else f'/research/{kind}/',
                        'resourceId': resource['id'], 'versionId': version['id']}}}
