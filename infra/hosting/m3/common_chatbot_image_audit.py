"""Final-release archive/import/Pod chain checks. No import or native startup."""
import argparse
import json
import re
from pathlib import Path

from access_image_audit import audit as local_audit
from image_audit import chain


def audit(role, source, build_directory, imported_directory, reference, pod=None):
    if role not in {"api", "runner", "web"} or not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError()
    repository = "docker.io/library/anxious-s13-chatbot-" + role + "-" + source[:7]
    root, imported = Path(build_directory), Path(imported_directory)
    local = local_audit(root / (role + ".tar"), root / (role + ".metadata.json"), root / (role + ".inspect.json"), repository, source)
    target, manifest, config = chain(imported, "")
    value = json.loads((imported / "config.json").read_text())
    if (reference != repository + "@" + target or manifest != local["arm64Manifest"] or config != local["configDigest"]
            or value["config"]["Labels"]["org.opencontainers.image.revision"] != source):
        raise ValueError()
    cri = json.loads((imported / "cri.json").read_text())["status"]
    if reference not in cri.get("repoDigests", []) or cri["id"] not in {target, manifest, config}:
        raise ValueError()
    if pod is not None:
        if pod["specImage"] != reference or pod["criId"] != cri["id"]:
            raise ValueError()
        identity = pod["podImageId"]
        for prefix in ("docker-pullable://", "containerd://", "docker://"):
            if identity.startswith(prefix): identity = identity[len(prefix):]; break
        if (identity.split("@", 1)[1] not in {target, manifest} if "@" in identity else identity != cri["id"]):
            raise ValueError()
    return {"role": role, "sourceSha": source, "reference": reference, "archiveSha256": local["archiveSha256"],
            "localTarget": local["targetDigest"], "importedTarget": target, "arm64Manifest": manifest, "configDigest": config,
            "podAssociationChecked": pod is not None, "nativeStarted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "runner", "web")); parser.add_argument("--source", required=True)
    parser.add_argument("--build", required=True); parser.add_argument("--imported", required=True); parser.add_argument("--reference", required=True)
    parser.add_argument("--pod")
    args = parser.parse_args()
    try:
        result = audit(args.role, args.source, args.build, args.imported, args.reference, json.loads(Path(args.pod).read_text()) if args.pod else None)
        print(json.dumps(result))
    except Exception:
        parser.exit(1, "Chatbot image chain audit refused; private metadata not printed\n")


if __name__ == "__main__":
    main()
