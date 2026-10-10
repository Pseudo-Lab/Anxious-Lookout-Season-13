"""Docker build-only pinned ARM64 executable installation, no authentication."""
import hashlib
import io
import os
import struct
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.160.1"
EXPECTED_SHA256 = "fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e"
URL = f"https://github.com/openai/codex/releases/download/rust-v{VERSION}/codex-aarch64-unknown-linux-musl.tar.gz"
HOST_SHA256 = "fbccde22982e3e679678e203a9c18eee8342fb04b096063d05159f1f80df4fd8"
HOST_ARCHIVE_SHA256 = "e5e027e6689efda2e3570aa600179f0ebb18632803350e152ed6c9b97dcf9741"
HOST_URL = f"https://github.com/openai/codex/releases/download/rust-v{VERSION}/codex-code-mode-host-aarch64-unknown-linux-musl.tar.gz"


def install_host():
    temporary = Path("/usr/local/bin/.codex-code-mode-host.pending")
    try:
        with urlopen(HOST_URL, timeout=60) as source:
            package = source.read(40_000_001)
        if len(package) > 40_000_000 or hashlib.sha256(package).hexdigest() != HOST_ARCHIVE_SHA256:
            raise RuntimeError("Code-mode host archive checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(package)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].name != "codex-code-mode-host-aarch64-unknown-linux-musl" or not 0 < members[0].size < 200_000_000:
                raise RuntimeError("Unexpected code-mode host artifact")
            executable = archive.extractfile(members[0]).read()
        if executable[:6] != b"\x7fELF\x02\x01" or struct.unpack("<H", executable[18:20])[0] != 183 or hashlib.sha256(executable).hexdigest() != HOST_SHA256:
            raise RuntimeError("Code-mode host executable checksum/architecture mismatch")
        temporary.write_bytes(executable)
        temporary.chmod(0o755)
        os.replace(temporary, "/usr/local/bin/codex-code-mode-host")
    finally:
        temporary.unlink(missing_ok=True)


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
    install_host()
