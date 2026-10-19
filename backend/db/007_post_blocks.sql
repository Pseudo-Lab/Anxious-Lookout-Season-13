CREATE TABLE research.post_markdown_backups (
 storage_table varchar(32) NOT NULL CHECK(storage_table IN ('post_versions','post_drafts','post_proposals','post_publications')),
 row_id uuid NOT NULL,
 markdown text NOT NULL,
 markdown_sha256 varchar(64) NOT NULL CHECK(markdown_sha256 ~ '^[a-f0-9]{64}$'),
 canonical_sha256 varchar(64) NOT NULL CHECK(canonical_sha256 ~ '^[a-f0-9]{64}$'),
 PRIMARY KEY(storage_table,row_id)
);
REVOKE ALL ON research.post_markdown_backups FROM PUBLIC,anxious_api;
