"""Research wiki rules (#105): one text for llm.md and both agent skills.

llm.md renders rules() at request time. The Codex and PAT SKILL.md files are
static; config_tests/test_research_wiki.py requires each to contain rules()
verbatim, so the vocabulary table cannot drift from app/relation_kinds.py.
"""
from .relation_kinds import markdown_table

RULES = """## Research wiki

Materials are wiki entries, one source each. Relations are reasoned links
between two items. Documents hold comparisons and syntheses across materials.

| What | Where |
|---|---|
| One source's summary, evidence and interpretation | that material's body |
| A link or contrast between two items | a relation (kind + description + evidence) |
| A comparison or synthesis across materials | a document (no type field; its title and summary say what it is) |

Never write a cross-material comparison into one material's body. Revise an
existing document on the same topic as a new version instead of adding another.

Material body template (Markdown headings; the server does not enforce it):
`## 핵심 질문`, `## 방법`, `## 결과`, `## 한계`, `## 원문 근거`, `## 해석`, `## 열린 질문`.
- What the source says is a quote with its location: `> … (p.12)` or `(§3.2)`.
- Your or the user's interpretation goes only under `## 해석`, as does any
  claim without source evidence.

Materials and documents have `summary`: one line, at most 300 characters.
Materials have `bibliography`, a CSL-JSON subset: type, title, author
([{family, given}] or [{literal}]), issued ({"date-parts": [[year, month, day]]},
month/day optional), container-title, publisher, volume, issue, page, number
(arXiv: "arXiv:<id>"), DOI (without https://doi.org/), ISBN, ISSN. Do not send
URL or accessed: sourceUrl and collectedAt are used. Other keys are rejected.
On updates, omitted summary/bibliography keep the latest values; null clears.

Relation kinds (source → target; the source is the item making the statement,
usually the new material). Only same_topic is undirected; omit `directed`.
Document–material relations go from the document with a directed kind.

<<RELATION_KINDS>>

Every relation needs a non-blank `description` saying why the items are linked.
Add `evidence` (at most 20): {itemId: the source or target, versionId: the
version of that item you read, locator: "p.12"/"§3.2", quote: at most 500
characters}; a locator or a quote is required. Older free-text kinds read as
기타; updating one requires a vocabulary kind with the same direction.

Ingest a new source:
1. Read research_list (the index: summary, relationCount, updatedAt) to find related items.
2. Save the material with the template, summary and bibliography, or update the existing one.
3. Revise related materials and documents as new versions; history keeps the old ones.
4. Relate the new material to existing items; record contradictions as refutes or disputes.
5. Run research_lint and fix what it reports. Semantic contradictions are yours to check."""


def rules():
    return RULES.replace("<<RELATION_KINDS>>", markdown_table())
