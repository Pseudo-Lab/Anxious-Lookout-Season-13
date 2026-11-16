ALTER TABLE research.versions
 ADD COLUMN author_kind varchar(16) NOT NULL DEFAULT 'human',
 ADD COLUMN author_conversation_id uuid,
 ADD COLUMN author_credential_id uuid,
 ADD CONSTRAINT versions_author_check CHECK (
   (author_kind = 'human' OR author_kind = 'unknown' OR author_kind = 'codex')
   AND ((author_kind = 'codex') = (author_conversation_id IS NOT NULL))
   AND author_credential_id IS NULL
 );
ALTER TABLE research.post_versions
 ADD COLUMN author_kind varchar(16) NOT NULL DEFAULT 'human',
 ADD COLUMN author_conversation_id uuid,
 ADD COLUMN author_credential_id uuid,
 ADD CONSTRAINT post_versions_author_check CHECK (
   (author_kind = 'human' OR author_kind = 'unknown' OR author_kind = 'codex')
   AND ((author_kind = 'codex') = (author_conversation_id IS NOT NULL))
   AND author_credential_id IS NULL
 );
-- Table SELECT/INSERT grants cover additive columns. Immutable history stays immutable.
-- Conversation ids intentionally have no cascading FK: provenance outlives archival.
-- credential_id is reserved; a future reviewed migration must enable PAT authors.
