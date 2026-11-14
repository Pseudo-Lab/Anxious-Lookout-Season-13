"""Private keyed fingerprints: recognize retired literals without retaining them.

Rolling hashes are only a search filter; HMAC verifies every match. This does
not claim recognition of arbitrary encodings or credentials never observed.
"""
import hashlib
import hmac
import json
import secrets

from app.codex_policy import CodexFailure

BASE, MASK = 257, (1 << 64) - 1
PROJECTION_VERSION = 4


def rolling(value):
    result = 0
    for char in value:
        result = (result * BASE + ord(char)) & MASK
    return result


def merge_spans(hits):
    """Sorted, merged [start, end) ranges; touching ranges merge too (start <= previous end), as text() always did."""
    merged = []
    for start, end in sorted(hits):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def redact(value, merged):
    """`value` with each merged range replaced by one marker."""
    if not merged:
        return value
    parts, previous = [], 0
    for start, end in merged:
        parts.extend((value[previous:start], "[redacted]"))
        previous = end
    return "".join(parts) + value[previous:]


class ProjectionJournal:
    VERSION = 1

    def __init__(self, payload, owner, trial):
        try:
            if payload["version"] != self.VERSION or payload["owner"] != owner or payload["trialId"] != trial:
                raise ValueError()
            self.key = bytes.fromhex(payload["key"])
            if len(self.key) != 32 or len(payload["values"]) > 512:
                raise ValueError()
            if not isinstance(payload.get("mac"), str) or not hmac.compare_digest(self.journal_mac({key: value for key, value in payload.items() if key != "mac"}), payload["mac"]):
                raise ValueError()
            self.values = {}
            for item in payload["values"]:
                length, filter_hash, digest = item
                if type(length) is not int or not 1 <= length <= 65536 or type(filter_hash) is not int or not 0 <= filter_hash <= MASK or len(bytes.fromhex(digest)) != 32:
                    raise ValueError()
                self.values.setdefault(length, {}).setdefault(filter_hash, set()).add(digest)
            self.owner, self.trial = owner, trial
        except Exception:
            raise CodexFailure("policy_refused") from None

    @classmethod
    def create(cls, owner, trial):
        payload = {"version": cls.VERSION, "owner": owner, "trialId": trial, "key": secrets.token_hex(32), "values": []}
        temporary = cls.__new__(cls)
        temporary.key = bytes.fromhex(payload["key"])
        payload["mac"] = temporary.journal_mac(payload)
        return cls(payload, owner, trial)

    def journal_mac(self, payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return hmac.new(self.key, b"journal-v1\0" + body, hashlib.sha256).hexdigest()

    def digest(self, value):
        return hmac.new(self.key, b"literal-v1\0" + value.encode("utf-8"), hashlib.sha256).hexdigest()

    def observe(self, literals):
        changed = False
        for value in literals:
            if not isinstance(value, str) or not 1 <= len(value) <= 65536:
                raise CodexFailure("policy_refused")
            bucket = self.values.setdefault(len(value), {}).setdefault(rolling(value), set())
            digest = self.digest(value)
            if digest not in bucket:
                bucket.add(digest)
                changed = True
        if sum(len(group) for lengths in self.values.values() for group in lengths.values()) > 512:
            raise CodexFailure("policy_refused")
        return changed

    def payload(self):
        payload = {"version": self.VERSION, "owner": self.owner, "trialId": self.trial, "key": self.key.hex(),
                   "values": [[length, filter_hash, digest] for length, groups in self.values.items() for filter_hash, values in groups.items() for digest in sorted(values)]}
        payload["mac"] = self.journal_mac(payload)
        return payload

    def spans(self, value):
        """The merged [start, end) ranges of every recorded literal in `value` (#90: the stream holdback needs the
        ranges, not only the redacted text)."""
        hits = []
        for length, groups in self.values.items():
            if length > len(value):
                continue
            factor = pow(BASE, length - 1, 1 << 64)
            current = rolling(value[:length])
            for offset in range(len(value) - length + 1):
                candidates = groups.get(current)
                if candidates and self.digest(value[offset:offset + length]) in candidates:
                    hits.append((offset, offset + length))
                if offset + length < len(value):
                    current = ((current - ord(value[offset]) * factor) * BASE + ord(value[offset + length])) & MASK
        return merge_spans(hits)

    def generation(self):
        """(literal count, longest literal length) of this journal: the stream holdback's L and its staleness check."""
        lengths = [length for length, groups in self.values.items() for group in groups.values() for _ in group]
        return len(lengths), max(lengths, default=0)

    def stream_rows(self, limit):
        """(longest length <= limit, {(length, digest)} of the longer literals): the r4 holdback's classification
        input (#90). The rows were verified with the journal MAC at load."""
        short = max((length for length in self.values if length <= limit), default=0)
        rows = {(length, digest) for length, groups in self.values.items() if length > limit
                for digests in groups.values() for digest in digests}
        return short, rows

    def text(self, value):
        return redact(value, self.spans(value))

    def sanitize(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.sanitize(entry) for entry in value]
        if isinstance(value, dict):
            return {self.text(key): self.sanitize(entry) for key, entry in value.items()}
        return value

    def projection_mac(self, session, thread, record):
        body = json.dumps([self.owner, self.trial, session, thread, record], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return hmac.new(self.key, b"projection-v4\0" + body, hashlib.sha256).hexdigest()
