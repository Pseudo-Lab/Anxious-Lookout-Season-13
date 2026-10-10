"""Docker-build only, official same-release companion with archive/ELF verification."""
import hashlib
import json
import struct
import tarfile
from pathlib import Path

archive = Path('/tmp/host.tar.gz')
assert hashlib.sha256(archive.read_bytes()).hexdigest() == 'e5e027e6689efda2e3570aa600179f0ebb18632803350e152ed6c9b97dcf9741'
assert hashlib.sha256(Path('/usr/local/bin/codex').read_bytes()).hexdigest() == 'fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e'
with tarfile.open(archive) as package:
    members = package.getmembers()
    assert len(members) == 1 and members[0].isfile()
    assert members[0].name == 'codex-code-mode-host-aarch64-unknown-linux-musl'
    assert 0 < members[0].size < 200_000_000
    data = package.extractfile(members[0]).read()
assert data[:6] == b'\x7fELF\x02\x01' and struct.unpack('<H', data[18:20])[0] == 183
target = Path('/usr/local/bin/codex-code-mode-host')
target.write_bytes(data)
target.chmod(0o755)
archive.unlink()
print(json.dumps({'companionBinarySha256': hashlib.sha256(data).hexdigest(),
                  'elfMachine': 'AArch64', 'mode': '0755', 'release': 'rust-v0.160.1'}))
