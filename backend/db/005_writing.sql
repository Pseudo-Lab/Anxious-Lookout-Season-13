CREATE TABLE research.document_drafts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.accounts(id),
  document_id uuid,
  kind varchar(16) NOT NULL DEFAULT 'document' CHECK(kind='document'),
  revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
  base_document_version integer CHECK(base_document_version>0),
  base_version_id uuid,
  title varchar(300) NOT NULL,
  content text NOT NULL CHECK(length(content)<=200000),
  state varchar(16) NOT NULL DEFAULT 'active' CHECK(state IN ('active','committed','discarded')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(document_id,owner_id,kind) REFERENCES research.items(id,owner_id,kind),
  FOREIGN KEY(base_version_id,document_id,owner_id,kind) REFERENCES research.versions(id,item_id,owner_id,kind),
  CHECK((document_id IS NULL AND base_document_version IS NULL AND base_version_id IS NULL) OR
        (document_id IS NOT NULL AND base_document_version IS NOT NULL AND base_version_id IS NOT NULL))
);
CREATE UNIQUE INDEX research_active_document_draft ON research.document_drafts(owner_id,document_id) WHERE state='active';
CREATE INDEX research_draft_owner ON research.document_drafts(owner_id,state,updated_at DESC,id DESC);
CREATE TABLE research.document_proposals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.accounts(id),
  document_id uuid NOT NULL,
  kind varchar(16) NOT NULL DEFAULT 'document' CHECK(kind='document'),
  version integer NOT NULL DEFAULT 1 CHECK(version>0),
  base_document_version integer NOT NULL CHECK(base_document_version>0),
  base_version_id uuid NOT NULL,
  title varchar(300) NOT NULL CHECK(length(btrim(title))>0),
  content text NOT NULL CHECK(length(content) BETWEEN 1 AND 200000),
  status varchar(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','discarded')),
  accepted_version_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(document_id,owner_id,kind) REFERENCES research.items(id,owner_id,kind),
  FOREIGN KEY(base_version_id,document_id,owner_id,kind) REFERENCES research.versions(id,item_id,owner_id,kind),
  FOREIGN KEY(accepted_version_id,document_id,owner_id,kind) REFERENCES research.versions(id,item_id,owner_id,kind),
  CHECK((status='accepted')=(accepted_version_id IS NOT NULL))
);
CREATE INDEX research_proposal_owner ON research.document_proposals(owner_id,document_id,updated_at DESC,id DESC);
REVOKE ALL ON research.document_drafts,research.document_proposals FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON research.document_drafts,research.document_proposals TO anxious_api;
