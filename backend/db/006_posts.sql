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
