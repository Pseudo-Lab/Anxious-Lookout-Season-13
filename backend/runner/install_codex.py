"""Docker build-only pinned Codex executable installation per platform, no authentication."""
import hashlib
import io
import os
import platform
import struct
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.160.1"
RELEASE = f"https://github.com/openai/codex/releases/download/rust-v{VERSION}"
# Same release, one exact artifact set per Docker platform. arm64 is the production
# node and its values are unchanged; amd64 is the x86_64 development VM. Any other
# platform, or a TARGETARCH that disagrees with the executing machine, refuses.
PINS = {
    "arm64": {
        "machine": "aarch64", "triple": "aarch64-unknown-linux-musl", "elf_machine": 183,
        "codex_sha256": "fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e",
        "host_sha256": "fbccde22982e3e679678e203a9c18eee8342fb04b096063d05159f1f80df4fd8",
        "host_archive_sha256": "e5e027e6689efda2e3570aa600179f0ebb18632803350e152ed6c9b97dcf9741",
    },
    "amd64": {
        "machine": "x86_64", "triple": "x86_64-unknown-linux-musl", "elf_machine": 62,
        "codex_sha256": "f34a4d2301892ae96c90097786bfe5dc269f187b6f69faf42a7b357b8c081e35",
        "host_sha256": "b33e8a5283f3c65c2a0aca6d43a59cfe850f624d8fa16992e3cad4fcc27c14e1",
        "host_archive_sha256": "8a69207d97545ac753b6585974e1e67a4c51ae5deacf06517db512bb25e0e3c2",
    },
}
BIN = Path("/usr/local/bin")


def pinned(architecture=None):
    """Pin set for a Docker architecture name, or for the executing machine."""
    if architecture is None:
        matches = [name for name, pin in PINS.items() if pin["machine"] == platform.machine()]
        architecture = matches[0] if len(matches) == 1 else None
    if architecture not in PINS:
        raise RuntimeError("Unsupported Codex platform")
    return PINS[architecture]


def check_elf(header, pin):
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or struct.unpack("<H", header[18:20])[0] != pin["elf_machine"]:
        raise RuntimeError("Codex executable architecture mismatch")


def install_host(pin, directory=BIN):
    name = f"codex-code-mode-host-{pin['triple']}"
    temporary = directory / ".codex-code-mode-host.pending"
    try:
        with urlopen(f"{RELEASE}/{name}.tar.gz", timeout=60) as source:
            package = source.read(40_000_001)
        if len(package) > 40_000_000 or hashlib.sha256(package).hexdigest() != pin["host_archive_sha256"]:
            raise RuntimeError("Code-mode host archive checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(package)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].name != name or not 0 < members[0].size < 200_000_000:
                raise RuntimeError("Unexpected code-mode host artifact")
            executable = archive.extractfile(members[0]).read()
        check_elf(executable, pin)
        if hashlib.sha256(executable).hexdigest() != pin["host_sha256"]:
            raise RuntimeError("Code-mode host executable checksum mismatch")
        temporary.write_bytes(executable)
        temporary.chmod(0o755)
        os.replace(temporary, directory / "codex-code-mode-host")
    finally:
        temporary.unlink(missing_ok=True)


def install(pin, directory=BIN):
    temporary = directory / ".codex.pending"
    try:
        with urlopen(f"{RELEASE}/codex-{pin['triple']}.tar.gz", timeout=60) as response, tarfile.open(fileobj=response, mode="r|gz") as archive:
            installed = False
            for entry in archive:
                if not entry.isfile() or Path(entry.name).name not in {"codex", f"codex-{pin['triple']}"}:
                    continue
                if installed or entry.size > 500_000_000:
                    raise RuntimeError("Unexpected Codex artifact")
                source = archive.extractfile(entry)
                digest = hashlib.sha256()
                header = b""
                with temporary.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        header = header or chunk[:20]
                        digest.update(chunk)
                        target.write(chunk)
                check_elf(header, pin)
                if digest.hexdigest() != pin["codex_sha256"]:
                    raise RuntimeError("Codex executable checksum mismatch")
                installed = True
            if not installed:
                raise RuntimeError("Codex executable missing")
        temporary.chmod(0o755)
        os.replace(temporary, directory / "codex")
    finally:
        temporary.unlink(missing_ok=True)


def build_pin(target_architecture):
    # BuildKit sets TARGETARCH; under qemu the executing machine is the target too.
    # A missing build arg must refuse rather than fall back to machine detection.
    if target_architecture not in PINS:
        raise RuntimeError("Unsupported Codex platform")
    pin = pinned(target_architecture)
    if pin is not pinned():
        raise RuntimeError("TARGETARCH does not match the build machine")
    return pin


if __name__ == "__main__":
    selected = build_pin(os.environ.get("TARGETARCH"))
    install(selected)
    install_host(selected)
