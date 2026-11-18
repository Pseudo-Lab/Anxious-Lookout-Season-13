"""Relation vocabulary (#105 W2): the single definition for API, UI and agent docs.

Kinds are stored as plain varchar(80) keys with no DB vocabulary CHECK, so keys
can be added or renamed here without a migration. Rows saved before #105 keep
their free-text kind; they read as legacy ("기타") until an update picks a key.
"""

POLARITY_GROUPS = (("positive", "지지·확장"), ("negative", "반박·이견"), ("neutral", "중립"))
LEGACY_LABEL = "기타"

# key, label (source side), inverse label (target side), polarity, directed, CiTO
KINDS = (
    ("supports", "지지함", "지지받음", "positive", True, ("supports", "citesAsEvidence")),
    ("confirms", "재현·확인함", "확인됨", "positive", True, ("confirms",)),
    ("extends", "확장함", "확장됨", "positive", True, ("extends",)),
    ("disputes", "이견 있음", "이견 받음", "negative", True, ("disputes", "disagreesWith")),
    ("refutes", "반박함", "반박됨", "negative", True, ("refutes",)),
    ("qualifies", "조건을 닮", "조건이 달림", "neutral", True, ("qualifies",)),
    ("uses_method", "방법 차용", "방법 제공", "neutral", True, ("usesMethodIn",)),
    ("uses_data", "데이터 사용", "데이터 제공", "neutral", True, ("usesDataFrom",)),
    ("updates", "갱신·대체함", "갱신됨", "neutral", True, ("updates",)),
    ("same_topic", "같은 주제", "같은 주제", "neutral", False, ("citesAsRelated",)),
)
KEYS = tuple(kind[0] for kind in KINDS)
DIRECTED = {kind[0]: kind[4] for kind in KINDS}
POLARITY = {kind[0]: kind[3] for kind in KINDS}


def known(kind):
    return kind in DIRECTED


def catalog():
    return {"items": [{"key": key, "label": label, "inverseLabel": inverse, "polarity": polarity,
                       "directed": directed, "cito": list(cito)}
                      for key, label, inverse, polarity, directed, cito in KINDS],
            "polarityGroups": [{"key": key, "label": label} for key, label in POLARITY_GROUPS],
            "legacyLabel": LEGACY_LABEL}


def markdown_table():
    """Agent-facing table generated from the same definition (llm.md, skill)."""
    rows = ["| key | forward / inverse | polarity | directed |", "|---|---|---|---|"]
    rows += [f"| `{key}` | {label} / {inverse} | {polarity} | {'yes' if directed else 'no'} |"
             for key, label, inverse, polarity, directed, _ in KINDS]
    return "\n".join(rows)
