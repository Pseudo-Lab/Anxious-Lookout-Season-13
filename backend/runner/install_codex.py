"""Docker build-only pinned ARM64 executable installation, no authentication."""
import hashlib
import os
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.160.1"
EXPECTED_SHA256 = "fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e"
URL = f"https://github.com/openai/codex/releases/download/rust-v{VERSION}/codex-aarch64-unknown-linux-musl.tar.gz"


def install():
    temporary = Path("/usr/local/bin/.codex.pending")
    try:
        with urlopen(URL, timeout=60) as response, tarfile.open(fileobj=response, mode="r|gz") as archive:
            installed = False
            for entry in archive:
                if not entry.isfile() or Path(entry.name).name not in {"codex", "codex-aarch64-unknown-linux-musl"}:
                    continue
                if installed or entry.size > 500_000_000:
                    raise RuntimeError("Unexpected Codex artifact")
                source = archive.extractfile(entry)
                digest = hashlib.sha256()
                with temporary.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        target.write(chunk)
                if digest.hexdigest() != EXPECTED_SHA256:
                    raise RuntimeError("Codex executable checksum mismatch")
                installed = True
            if not installed:
                raise RuntimeError("Codex executable missing")
        temporary.chmod(0o755)
        os.replace(temporary, "/usr/local/bin/codex")
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    install()
