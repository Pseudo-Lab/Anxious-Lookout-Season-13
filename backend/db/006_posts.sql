CREATE TABLE research.posts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid REFERENCES auth.accounts(id),
 legacy_import boolean NOT NULL DEFAULT false,
 slug varchar(100) NOT NULL UNIQUE CHECK(slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
 version integer NOT NULL DEFAULT 1 CHECK(version>0),
 content_number integer NOT NULL DEFAULT 1 CHECK(content_number>0),
 archived boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id),
 CHECK((owner_id IS NULL)=legacy_import)
);
CREATE TABLE research.post_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 post_id uuid NOT NULL REFERENCES research.posts(id),
 number integer NOT NULL CHECK(number>0),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(post_id,number), UNIQUE(id,post_id)
);
CREATE TABLE research.post_drafts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL REFERENCES auth.accounts(id),
 post_id uuid,
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 base_post_version integer CHECK(base_post_version>0),
 base_version_id uuid,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 state varchar(16) NOT NULL DEFAULT 'active' CHECK(state IN ('active','committed','discarded')),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(post_id,owner_id) REFERENCES research.posts(id,owner_id),
 FOREIGN KEY(base_version_id,post_id) REFERENCES research.post_versions(id,post_id),
 CHECK((post_id IS NULL AND base_post_version IS NULL AND base_version_id IS NULL) OR
       (post_id IS NOT NULL AND base_post_version IS NOT NULL AND base_version_id IS NOT NULL))
);
CREATE UNIQUE INDEX research_active_post_draft ON research.post_drafts(owner_id,post_id) WHERE state='active';
CREATE INDEX research_post_owner ON research.posts(owner_id,updated_at DESC,id DESC);
CREATE INDEX research_post_draft_owner ON research.post_drafts(owner_id,state,updated_at DESC,id DESC);
CREATE INDEX research_post_version_listing ON research.post_versions(post_id,updated_at DESC,id DESC);
REVOKE ALL ON research.posts,research.post_versions,research.post_drafts FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON research.posts,research.post_drafts TO anxious_api;
GRANT SELECT,INSERT ON research.post_versions TO anxious_api;

CREATE TABLE research.post_proposals (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL REFERENCES auth.accounts(id),
 post_id uuid,
 version integer NOT NULL DEFAULT 1 CHECK(version>0),
 base_post_version integer CHECK(base_post_version>0),
 base_version_id uuid,
 base_draft_id uuid REFERENCES research.post_drafts(id),
 base_draft_revision integer CHECK(base_draft_revision>0),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 status varchar(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','discarded')),
 accepted_post_id uuid,
 accepted_version_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(post_id,owner_id) REFERENCES research.posts(id,owner_id),
 FOREIGN KEY(base_version_id,post_id) REFERENCES research.post_versions(id,post_id),
 FOREIGN KEY(accepted_post_id,owner_id) REFERENCES research.posts(id,owner_id),
 FOREIGN KEY(accepted_version_id,accepted_post_id) REFERENCES research.post_versions(id,post_id),
 CHECK((post_id IS NULL AND base_post_version IS NULL AND base_version_id IS NULL) OR
       (post_id IS NOT NULL AND base_post_version IS NOT NULL AND base_version_id IS NOT NULL)),
 CHECK((base_draft_id IS NULL)=(base_draft_revision IS NULL)),
 CHECK((status='accepted')=(accepted_version_id IS NOT NULL)),
 CHECK((accepted_post_id IS NULL)=(accepted_version_id IS NULL))
);
CREATE INDEX research_post_proposal_owner ON research.post_proposals(owner_id,updated_at DESC,id DESC);
REVOKE ALL ON research.post_proposals FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON research.post_proposals TO anxious_api;
ALTER TABLE research.document_proposals ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE research.document_proposals ALTER COLUMN base_document_version DROP NOT NULL;
ALTER TABLE research.document_proposals ALTER COLUMN base_version_id DROP NOT NULL;
ALTER TABLE research.document_proposals ADD CONSTRAINT document_proposal_base CHECK(
 (document_id IS NULL AND base_document_version IS NULL AND base_version_id IS NULL) OR
 (document_id IS NOT NULL AND base_document_version IS NOT NULL AND base_version_id IS NOT NULL));

CREATE TABLE research.post_publications (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 post_id uuid NOT NULL REFERENCES research.posts(id),
 version_id uuid NOT NULL,
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,post_id),
 FOREIGN KEY(version_id,post_id) REFERENCES research.post_versions(id,post_id)
);
CREATE TABLE research.post_publication_heads (
 post_id uuid PRIMARY KEY REFERENCES research.posts(id),
 publication_id uuid,
 FOREIGN KEY(publication_id,post_id) REFERENCES research.post_publications(id,post_id)
);
CREATE INDEX research_post_public_listing ON research.post_publications(updated_at DESC,id DESC);
REVOKE ALL ON research.post_publications,research.post_publication_heads FROM PUBLIC;
GRANT SELECT,INSERT ON research.post_publications TO anxious_api;
GRANT SELECT,INSERT,UPDATE ON research.post_publication_heads TO anxious_api;

CREATE TABLE research.post_images (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL REFERENCES auth.accounts(id),
 mime_type varchar(32) NOT NULL CHECK(mime_type IN ('image/png','image/jpeg','image/webp')),
 size integer NOT NULL CHECK(size BETWEEN 1 AND 5242880),
 width integer NOT NULL CHECK(width BETWEEN 1 AND 8192),
 height integer NOT NULL CHECK(height BETWEEN 1 AND 8192),
 sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 deleted boolean NOT NULL DEFAULT false,
 purged boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(width::bigint*height<=16000000),
 CHECK(NOT purged OR deleted)
);
CREATE INDEX research_post_image_owner ON research.post_images(owner_id,updated_at DESC,id DESC);
ALTER TABLE research.post_versions ADD COLUMN image_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE research.post_drafts ADD COLUMN image_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE research.post_proposals ADD COLUMN image_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE research.post_publications ADD COLUMN image_ids uuid[] NOT NULL DEFAULT '{}';
REVOKE ALL ON research.post_images FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON research.post_images TO anxious_api;
