"""Per-platform Codex pin selection with synthetic archives; no network or real release."""
import hashlib
import io
import struct
import tarfile

import pytest

from runner import install_codex


def test_pin_sets_are_complete_and_production_arm64_is_unchanged():
    assert set(install_codex.PINS) == {"arm64", "amd64"}
    assert install_codex.PINS["arm64"] == {
        "machine": "aarch64", "triple": "aarch64-unknown-linux-musl", "elf_machine": 183,
        "codex_sha256": "fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e",
        "host_sha256": "fbccde22982e3e679678e203a9c18eee8342fb04b096063d05159f1f80df4fd8",
        "host_archive_sha256": "e5e027e6689efda2e3570aa600179f0ebb18632803350e152ed6c9b97dcf9741",
    }
    amd64 = install_codex.PINS["amd64"]
    assert (amd64["machine"], amd64["triple"], amd64["elf_machine"]) == ("x86_64", "x86_64-unknown-linux-musl", 62)
    for pin in install_codex.PINS.values():
        assert all(len(pin[key]) == 64 for key in ("codex_sha256", "host_sha256", "host_archive_sha256"))


@pytest.mark.parametrize("machine, expected", [("aarch64", "arm64"), ("x86_64", "amd64")])
def test_machine_and_build_arg_must_agree(monkeypatch, machine, expected):
    monkeypatch.setattr(install_codex.platform, "machine", lambda: machine)
    assert install_codex.pinned() is install_codex.PINS[expected]
    assert install_codex.build_pin(expected) is install_codex.PINS[expected]
    other = ({"arm64", "amd64"} - {expected}).pop()
    for refused in (other, None, "", "s390x"):
        with pytest.raises(RuntimeError):
            install_codex.build_pin(refused)


def test_unsupported_machine_refuses(monkeypatch):
    monkeypatch.setattr(install_codex.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError):
        install_codex.pinned()


def executable(elf_machine):
    return b"\x7fELF\x02\x01" + b"\0" * 12 + struct.pack("<H", elf_machine) + b"synthetic body"


def packaged(name, data):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(data), 0o755
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def synthetic(monkeypatch, architecture, codex_machine=None, host_machine=None):
    pin = dict(install_codex.PINS[architecture])
    codex, host = executable(codex_machine or pin["elf_machine"]), executable(host_machine or pin["elf_machine"])
    archives = {
        f"{install_codex.RELEASE}/codex-{pin['triple']}.tar.gz": packaged(f"codex-{pin['triple']}", codex),
        f"{install_codex.RELEASE}/codex-code-mode-host-{pin['triple']}.tar.gz": packaged(f"codex-code-mode-host-{pin['triple']}", host),
    }
    requested = []

    def fetch(url, timeout):
        requested.append(url)
        return io.BytesIO(archives[url])

    monkeypatch.setattr(install_codex, "urlopen", fetch)
    pin.update(codex_sha256=hashlib.sha256(codex).hexdigest(), host_sha256=hashlib.sha256(host).hexdigest(),
               host_archive_sha256=hashlib.sha256(archives[list(archives)[1]]).hexdigest())
    return pin, codex, host, requested


@pytest.mark.parametrize("architecture", ["arm64", "amd64"])
def test_installs_only_the_selected_platform_artifacts(tmp_path, monkeypatch, architecture):
    pin, codex, host, requested = synthetic(monkeypatch, architecture)
    install_codex.install(pin, tmp_path)
    install_codex.install_host(pin, tmp_path)
    assert (tmp_path / "codex").read_bytes() == codex and (tmp_path / "codex-code-mode-host").read_bytes() == host
    assert all(pin["triple"] in url for url in requested) and len(requested) == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == ["codex", "codex-code-mode-host"]


@pytest.mark.parametrize("damage", ["codex-elf", "host-elf", "codex-sha", "host-sha", "host-archive-sha"])
def test_wrong_architecture_or_checksum_installs_nothing(tmp_path, monkeypatch, damage):
    foreign = install_codex.PINS["arm64"]["elf_machine"]
    pin, *_ = synthetic(monkeypatch, "amd64", codex_machine=foreign if damage == "codex-elf" else None,
                        host_machine=foreign if damage == "host-elf" else None)
    if damage.endswith("-sha"):
        pin[{"codex-sha": "codex_sha256", "host-sha": "host_sha256", "host-archive-sha": "host_archive_sha256"}[damage]] = "0" * 64
    with pytest.raises(RuntimeError):
        install_codex.install(pin, tmp_path) if damage.startswith("codex") else install_codex.install_host(pin, tmp_path)
    assert list(tmp_path.iterdir()) == []
