"""Stream a stopped native/control backup through private exec stdout; no restore."""
import argparse
import json
import os
import stat
import sys
import tarfile
from pathlib import Path


def entries(root):
    if root.resolve() != root or not root.is_dir() or root.stat().st_uid != os.getuid():
        raise ValueError()
    result, size = [], 0
    for directory, names, files in os.walk(root, followlinks=False):
        for name in sorted(names + files):
            path = Path(directory) / name
            info = path.lstat()
            # A stopped SupplySocket may have left this exact stale socket.
            if stat.S_ISSOCK(info.st_mode) and path == root / "access.sock":
                continue
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or info.st_uid != os.getuid():
                raise ValueError()
            size += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if size > 2_000_000_000:
                raise ValueError()
            result.append((path, (info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)))
    return result


def stream(native, control, output):
    inventory = {"native": (native, entries(native)), "control": (control, entries(control))}
    with tarfile.open(fileobj=output, mode="w|", dereference=True) as archive:
        for label, (root, files) in inventory.items():
            archive.add(root, arcname=label, recursive=False)
            for path, _ in files:
                archive.add(path, arcname=label + "/" + path.relative_to(root).as_posix(), recursive=False)
    for root, before in inventory.values():
        if entries(root) != before:
            raise ValueError("State changed during backup")


def verify(path):
    # List/check only. Never extract over live native/control state or a ledger.
    roots, names = set(), set()
    with tarfile.open(path, "r:") as archive:
        for item in archive:
            parts = Path(item.name).parts
            if not parts or parts[0] not in {"native", "control"} or ".." in parts or Path(item.name).is_absolute() or item.name in names or not (item.isfile() or item.isdir()):
                raise ValueError()
            roots.add(parts[0]); names.add(item.name)
            if item.isfile():
                source = archive.extractfile(item)
                remaining = item.size
                while remaining:
                    block = source.read(min(remaining, 1_000_000))
                    if not block: raise ValueError()
                    remaining -= len(block)
    if roots != {"native", "control"} or "native/state-binding.json" not in names or "native/account-id" not in names:
        raise ValueError()
    return {"nativeControlArchiveVerified": True, "entries": len(names), "restored": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("stream", "verify"))
    parser.add_argument("--archive")
    args = parser.parse_args()
    try:
        if args.mode == "stream": stream(Path("/native/access-web"), Path("/control/access-web"), sys.stdout.buffer)
        else: print(json.dumps(verify(args.archive)))
    except Exception:
        raise SystemExit("Chatbot archive failed; preserve partial output privately, do not use or retry automatically")


if __name__ == "__main__":
    main()
