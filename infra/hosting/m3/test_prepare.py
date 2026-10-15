import json
import hashlib
from copy import deepcopy
from pathlib import Path
import pytest
from prepare import patch, jobs, API, OLD_API
import image_audit


def baseline():
    return {"kind":"Deployment","metadata":{"name":"api","namespace":"m2-hosting","uid":"owned","resourceVersion":"10"},"spec":{
        "replicas":1,"strategy":{"type":"RollingUpdate","rollingUpdate":{"maxSurge":1,"maxUnavailable":0}},"template":{"spec":{
            "containers":[{"name":"api","image":OLD_API,"env":[{"name":"DATABASE_URL_FILE","value":"/run/secrets/database-url"}],
                "envFrom":[{"configMapRef":{"name":"hosting-config"}}]}],"volumes":[{"name":"credentials","secret":{"secretName":"hosting-api"}}]}}}}


def apply(value, operations):
    value=deepcopy(value)
    for operation in operations:
        keys=operation["path"].strip("/").split("/")
        target=value
        for key in keys[:-1]: target=target[int(key)] if isinstance(target,list) else target[key]
        key=int(keys[-1]) if isinstance(target,list) else keys[-1]
        if operation["op"]=="test": assert target[key]==operation["value"]
        else: target[key]=operation["value"]
    return value


def test_forward_and_rollback_preserve_existing_spec_and_auth_refs():
    old=baseline(); new=apply(old,patch(old,old));new["metadata"]["resourceVersion"]="20"
    assert new["spec"]["template"]["spec"]["containers"][0]["image"]==API
    env={v["name"]:v["value"] for v in new["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["CODEX_PERSONAL_ENABLE"]=="false" and env["CODEX_RUNNERS_FILE"]==""
    restored=apply(new,patch(old,new,True))
    assert restored["spec"]==old["spec"]


def kube_env_omission(value):
    """Observed GET shape: empty literal values disappear, env names remain."""
    value = deepcopy(value)
    for env in value["spec"]["template"]["spec"]["containers"][0]["env"]:
        if env.get("value") == "":
            env.pop("value")
    return value


def test_m3_rollback_after_kubernetes_empty_env_omission_restores_original():
    old = baseline()
    old["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "UNRELATED_EMPTY", "value": ""})
    current = kube_env_omission(apply(old, patch(old, old)))
    current["metadata"]["resourceVersion"] = "observed-20"
    env = {v["name"]: v for v in current["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["CODEX_RUNNERS_FILE"] == {"name": "CODEX_RUNNERS_FILE"}
    assert env["CODEX_PERSONAL_ACCOUNT_ID"] == {"name": "CODEX_PERSONAL_ACCOUNT_ID"}
    operations = patch(old, current, True)
    assert operations[1]["value"] == "observed-20"
    assert apply(current, operations)["spec"] == old["spec"]
    # Forward comparison also accepts only the server's empty-literal omission.
    assert patch(old, kube_env_omission(old))


@pytest.mark.parametrize("change", ["valueFrom", "nonempty", "false", "null", "auth-ref", "duplicate", "order", "volume"])
def test_m3_rollback_omission_does_not_hide_real_spec_changes(change):
    old = baseline()
    current = kube_env_omission(apply(old, patch(old, old)))
    container = current["spec"]["template"]["spec"]["containers"][0]
    env = next(v for v in container["env"] if v["name"] == "CODEX_RUNNERS_FILE")
    if change == "valueFrom":
        env.update(value="", valueFrom={"secretKeyRef": {"name": "other", "key": "x"}})
    elif change == "nonempty": env["value"] = "/other-map"
    elif change == "false": next(v for v in container["env"] if v["name"] == "CODEX_PERSONAL_ENABLE")["value"] = "true"
    elif change == "null": env["value"] = None
    elif change == "auth-ref": container["env"][0]["value"] = "/other-secret"
    elif change == "duplicate": container["env"].append(deepcopy(env))
    elif change == "order": container["env"].reverse()
    else: current["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"] = "other"
    with pytest.raises(ValueError): patch(old, current, True)


@pytest.mark.parametrize("field",["uid","strategy","image","auth-ref","enabled","map"])
def test_changed_baseline_refuses(field):
    old=baseline();now=deepcopy(old)
    if field=="uid": now["metadata"]["uid"]="foreign"
    elif field=="strategy": now["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"]=1
    elif field=="image": now["spec"]["template"]["spec"]["containers"][0]["image"]="other"
    elif field=="auth-ref": now["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"]="other"
    else:
        old["spec"]["template"]["spec"]["containers"][0]["env"].append({"name":"CODEX_PERSONAL_ENABLE" if field=="enabled" else "CODEX_RUNNERS_FILE","value":"true" if field=="enabled" else "/real-map"})
        now=deepcopy(old)
    with pytest.raises(ValueError):patch(old,now)


def test_jobs_one_shot_existing_secrets_correct_revision_and_no_runner():
    objects=jobs()
    assert objects[0]["immutable"] is True
    assert len(objects)==4 and all(v["metadata"]["namespace"]=="m2-hosting" for v in objects)
    migration=objects[2]["spec"]
    assert migration["backoffLimit"]==0 and migration["activeDeadlineSeconds"]==180
    pod=migration["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False and pod["securityContext"]["runAsUser"]==10001
    assert pod["containers"][0]["command"]==["python","-m","app.migrate","--revision","0004_publication"]
    assert all(any(v["name"]=="PYTHONPATH" and v["value"]=="/app" for v in item["spec"]["template"]["spec"]["containers"][0]["env"]) for item in objects[1:])
    assert {v["secret"]["secretName"] for v in pod["volumes"] if "secret" in v}=={"hosting-api","hosting-ops"}


def chain_fixture(root):
    def write(name,data):
        payload=json.dumps(data).encode();(root/name).write_bytes(payload)
        return "sha256:"+hashlib.sha256(payload).hexdigest()
    config={"os":"linux","architecture":"arm64"}
    c=write("local-config.json",config)
    manifest={"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","config":{"digest":c},"layers":[{"digest":"sha256:"+"a"*64}]}
    m=write("local-manifest.json",manifest)
    index={"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[{"digest":m,"platform":{"os":"linux","architecture":"arm64"}}]}
    i=write("local-target.json",index)
    for name in ("config.json","manifest.json","target.json"):(root/name).write_bytes((root/("local-"+name)).read_bytes())
    ref="docker.io/library/anxious-s13-personal-web@"+i
    write("cri.json",{"status":{"id":c,"repoDigests":[ref]}})
    return i,ref


def test_root_audit_uses_root_policy_not_b19(tmp_path,monkeypatch):
    local,reference=chain_fixture(tmp_path)
    monkeypatch.setitem(image_audit.POLICY,"root-web",("edff-source",local,"docker.io/library/anxious-s13-personal-web"))
    result=image_audit.audit("root-web",reference,tmp_path)
    assert result["sourceSha"]=="edff-source" and result["podAssociationChecked"] is False
    with pytest.raises(ValueError):image_audit.audit("api",reference,tmp_path)


def test_root_audit_rejects_changed_layer_chain(tmp_path,monkeypatch):
    local,reference=chain_fixture(tmp_path)
    monkeypatch.setitem(image_audit.POLICY,"root-web",("edff-source",local,"docker.io/library/anxious-s13-personal-web"))
    data=json.loads((tmp_path/"manifest.json").read_text());data["layers"][0]["digest"]="sha256:"+"b"*64
    payload=json.dumps(data).encode();(tmp_path/"manifest.json").write_bytes(payload)
    index=json.loads((tmp_path/"target.json").read_text());index["manifests"][0]["digest"]="sha256:"+hashlib.sha256(payload).hexdigest()
    (tmp_path/"target.json").write_text(json.dumps(index))
    with pytest.raises(ValueError):image_audit.audit("root-web",reference,tmp_path)
