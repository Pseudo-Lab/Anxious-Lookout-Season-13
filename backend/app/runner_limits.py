"""Configurable #16 capacity/idle inputs; reading settings never changes live quota."""
from dataclasses import dataclass
import os
import re

from runner.lifecycle import require


@dataclass(frozen=True)
class RunnerLimits:
    max_pods: int = 3
    idle_seconds: int = 1800

    def __post_init__(self):
        require(type(self.max_pods) is int and self.max_pods >= 0)
        require(type(self.idle_seconds) is int and self.idle_seconds > 0)


def load_limits():
    values = []
    for key, default in [('CODEX_USER_RUNNER_MAX_PODS', '3'),
                         ('CODEX_USER_RUNNER_IDLE_SECONDS', '1800')]:
        raw = os.getenv(key, default)
        require(re.fullmatch(r'0|[1-9][0-9]{0,8}', raw) is not None)
        values.append(int(raw))
    return RunnerLimits(*values)
