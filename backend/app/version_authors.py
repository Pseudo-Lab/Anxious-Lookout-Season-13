"""Version provenance is server authority, separate from the resource's owner."""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class VersionAuthor:
    conversation_id: UUID | None = None
    credential_id: UUID | None = None
    name: str | None = None

    def columns(self):
        if bool(self.credential_id) != bool(self.name) or (self.credential_id and self.conversation_id):
            raise ValueError('Invalid server author')
        return {'author_kind': 'pat' if self.credential_id else 'codex' if self.conversation_id else 'human',
                'author_conversation_id': self.conversation_id, 'author_credential_id': self.credential_id,
                'author_name': self.name}


def value(row):
    conversation = getattr(row, 'author_conversation_id', None)
    result = {'kind': getattr(row, 'author_kind', None) or 'human',
              'conversationId': str(conversation) if conversation else None}
    if result['kind'] == 'pat':
        result.update(credentialId=str(row.author_credential_id), name=row.author_name)
    return result


def saved(resource, kind):
    version = resource['latestVersion']
    return {**resource, 'saved': {
        'resourceType': kind, 'id': resource['id'],
        'title': version['title'] if kind == 'post' else resource['title'],
        'versionId': version['id'], 'versionNumber': version['number'],
        'destination': {'path': '/write/' if kind == 'post' else f'/research/{kind}/',
                        'resourceId': resource['id'], 'versionId': version['id']}}}
