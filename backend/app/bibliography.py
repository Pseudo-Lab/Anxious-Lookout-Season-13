"""Material bibliography (#105 W1): CSL-JSON subset constants and lint completeness.

URL and accessed are deliberately absent: the material's sourceUrl and
collectedAt are the single source for them (PM decision 3).
"""

TYPES = ("article-journal", "paper-conference", "article", "book", "chapter", "report", "thesis",
         "webpage", "post-weblog", "article-newspaper", "article-magazine", "dataset", "software",
         "motion_picture", "interview", "legislation", "document")

# Kinds that normally carry a standard identifier. Other kinds rely on sourceUrl,
# so lint does not report a missing identifier for them (contract v1.1).
IDENTIFIER_EXPECTED = frozenset({"article-journal", "paper-conference", "article", "book", "chapter",
                                 "report", "dataset"})
IDENTIFIERS = ("DOI", "ISBN", "ISSN", "number")


def missing_fields(bibliography):
    """Deterministic completeness check over a stored (by-alias) bibliography."""
    if bibliography is None:
        return ["bibliography"]
    missing = [field for field in ("title", "author", "issued") if not bibliography.get(field)]
    if bibliography.get("type") in IDENTIFIER_EXPECTED and not any(bibliography.get(key) for key in IDENTIFIERS):
        missing.append("identifier")
    return missing
