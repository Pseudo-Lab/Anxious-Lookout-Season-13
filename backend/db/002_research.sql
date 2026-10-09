-- Additive M3 schema. auth data and its Alembic marker remain untouched.
CREATE SCHEMA research;
CREATE TABLE research.items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.accounts(id),
  kind varchar(16) NOT NULL CHECK (kind IN ('material','document')),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  content_number integer NOT NULL DEFAULT 1 CHECK (content_number > 0),
  archived boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, owner_id), UNIQUE(id, owner_id, kind)
);
CREATE INDEX research_items_owner ON research.items(owner_id,kind,archived,updated_at DESC,id DESC);
CREATE TABLE research.versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id uuid NOT NULL,
  owner_id uuid NOT NULL,
  kind varchar(16) NOT NULL,
  number integer NOT NULL CHECK (number > 0),
  title varchar(300) NOT NULL CHECK(length(btrim(title)) > 0),
  content text NOT NULL CHECK(length(content) BETWEEN 1 AND 200000),
  source_url text,
  collected_at timestamptz,
  content_kind varchar(16),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(item_id,owner_id,kind) REFERENCES research.items(id,owner_id,kind),
  CHECK ((kind='material' AND source_url IS NOT NULL AND collected_at IS NOT NULL
          AND content_kind IN ('full','excerpt','summary')) OR
         (kind='document' AND source_url IS NULL AND collected_at IS NULL AND content_kind IS NULL)),
  UNIQUE(item_id,number), UNIQUE(id,item_id,owner_id,kind)
);
CREATE TABLE research.relations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.accounts(id),
  source_id uuid NOT NULL, source_kind varchar(16) NOT NULL,
  target_id uuid NOT NULL, target_kind varchar(16) NOT NULL,
  kind varchar(80) NOT NULL CHECK(length(btrim(kind)) > 0),
  description text NOT NULL CHECK(length(description) <= 4000),
  directed boolean NOT NULL,
  archived boolean NOT NULL DEFAULT false,
  version integer NOT NULL DEFAULT 1 CHECK(version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(source_id,owner_id,source_kind) REFERENCES research.items(id,owner_id,kind),
  FOREIGN KEY(target_id,owner_id,target_kind) REFERENCES research.items(id,owner_id,kind),
  CHECK(source_id<>target_id),
  CHECK((source_kind=target_kind) OR (source_kind='document' AND target_kind='material' AND directed)),
  CHECK(directed OR source_id<target_id)
);
CREATE UNIQUE INDEX research_relation_unique ON research.relations(owner_id,source_id,target_id,kind,directed) WHERE NOT archived;
CREATE INDEX research_relation_source ON research.relations(owner_id,source_id);
CREATE INDEX research_relation_target ON research.relations(owner_id,target_id);
CREATE TABLE research.idempotency (
  owner_id uuid NOT NULL REFERENCES auth.accounts(id),
  key uuid NOT NULL,
  fingerprint varchar(64) NOT NULL,
  status integer NOT NULL,
  response jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(owner_id,key)
);
REVOKE ALL ON SCHEMA research FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA research FROM PUBLIC;
GRANT USAGE ON SCHEMA research TO anxious_api;
GRANT SELECT, INSERT, UPDATE ON research.items,research.relations TO anxious_api;
GRANT SELECT, INSERT ON research.versions,research.idempotency TO anxious_api;
