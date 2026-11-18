-- #105 research wiki. Additive, forward-only; existing rows read as NULL / [].
-- summary: one-line summary per material/document version (trimmed by the API).
-- bibliography: CSL-JSON subset per material version; shape is validated by the API.
ALTER TABLE research.versions
 ADD COLUMN summary varchar(300),
 ADD COLUMN bibliography jsonb,
 ADD CONSTRAINT versions_wiki_check CHECK (
   (summary IS NULL OR length(btrim(summary)) > 0)
   AND (bibliography IS NULL OR (kind = 'material' AND jsonb_typeof(bibliography) = 'object'))
 );
-- evidence: [{itemId, versionId, versionNumber, locator, quote}], at most 20.
-- Endpoint/version/owner membership is checked by the API under the graph lock.
ALTER TABLE research.relations
 ADD COLUMN evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
 ADD CONSTRAINT relations_evidence_check CHECK (
   CASE WHEN jsonb_typeof(evidence) = 'array' THEN jsonb_array_length(evidence) <= 20 ELSE false END
 );
-- Existing table grants (versions SELECT/INSERT, relations SELECT/INSERT/UPDATE) cover the new columns.
-- Relation kinds stay free varchar(80): the vocabulary is enforced in app/relation_kinds.py (W2).
