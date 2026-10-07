import json
import os
import re
from datetime import datetime
from pathlib import Path


def main():
    sha = os.environ.get("APP_GIT_SHA", "")
    built = os.environ.get("APP_BUILT_AT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", built):
        raise ValueError("Valid immutable release metadata is required")
    datetime.fromisoformat(built.replace("Z", "+00:00"))
    Path("/app/release.json").write_text(json.dumps({"sha": sha, "builtAt": built}))


if __name__ == "__main__":
    main()
