"""Offline exact API-b19/root-web-edff provenance. No caller policy overrides."""
import hashlib
import json
import re
import sys
from pathlib import Path

POLICY = {
    "api": ("b19fafe8187fd37b7c11fcfadde40893c8ab3a9c", "sha256:41403b6a5ab16bee5cfb936879761f8a31420085236794de347eb410944defca", "docker.io/library/anxious-s13-personal-api"),
    "root-web": ("edff9cefe26b58d51619238bb12538a94249a9e9", "sha256:02b39712fd75b482b7e966008eb76ee2bf97304dbc0752af0e304c1e429afa0e", "docker.io/library/anxious-s13-personal-web"),
}


def require(value):
    if not value: raise ValueError("Image provenance mismatch")


def blob(path):
    data=Path(path).read_bytes()
    return "sha256:"+hashlib.sha256(data).hexdigest(),json.loads(data)


def chain(root,prefix):
    target,target_data=blob(root/(prefix+"target.json"))
    manifest,manifest_data=blob(root/(prefix+"manifest.json"))
    config,config_data=blob(root/(prefix+"config.json"))
    require(target_data.get("schemaVersion")==2 and manifest_data.get("schemaVersion")==2)
    if "manifests" in target_data:
        require(target_data.get("mediaType") in {"application/vnd.oci.image.index.v1+json","application/vnd.docker.distribution.manifest.list.v2+json"})
        matches=[v for v in target_data["manifests"] if v.get("platform",{}).get("os")=="linux" and v["platform"].get("architecture")=="arm64" and v["platform"].get("variant","") in {"","v8"}]
        require(len(matches)==1 and matches[0]["digest"]==manifest)
    else: require(target==manifest)
    require(manifest_data.get("mediaType") in {"application/vnd.oci.image.manifest.v1+json","application/vnd.docker.distribution.manifest.v2+json"})
    require(manifest_data["config"]["digest"]==config and config_data["os"]=="linux" and config_data["architecture"]=="arm64")
    require(manifest_data.get("layers") and all(re.fullmatch(r"sha256:[0-9a-f]{64}",v["digest"]) for v in manifest_data["layers"]))
    return target,manifest,config


def audit(role,reference,directory,pod=None):
    source,approved_id,repository=POLICY[role]
    root=Path(directory)
    local=chain(root,"local-"); imported=chain(root,"")
    # Docker ID may be an index or config ID. Bind the actual recorded descriptor
    # chain to the independently approved ID rather than guessing its media type.
    require(approved_id in {local[0],local[2]} and imported[1:]==local[1:])
    require(reference==repository+"@"+imported[0])
    status=json.loads((root/"cri.json").read_text())["status"]
    require(reference in status.get("repoDigests",[]) and status["id"] in imported)
    if pod is not None:
        data=json.loads(Path(pod).read_text())
        require(data["specImage"]==reference and data["criId"]==status["id"])
        value=data["podImageId"]
        for prefix in ("docker-pullable://","containerd://","docker://"):
            if value.startswith(prefix): value=value[len(prefix):];break
        require(value.split("@",1)[1] in imported[:2] if "@" in value else value==status["id"])
    return {"status":"ok","role":role,"sourceSha":source,"approvedDockerId":approved_id,
        "localTarget":local[0],"localManifest":local[1],"configDigest":imported[2],"containerdTarget":imported[0],
        "arm64Manifest":imported[1],"reference":reference,"podAssociationChecked":pod is not None,
        "locallyImportedOnly":True,"nativeStarted":False}


if __name__=="__main__":
    try: print(json.dumps(audit(*sys.argv[1:])))
    except Exception:
        print("Root rollout image audit refused; no metadata values printed",file=sys.stderr);sys.exit(1)
