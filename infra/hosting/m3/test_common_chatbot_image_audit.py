"""Tiny synthetic OCI archives and import metadata, no production builds/import."""
import hashlib
import io
import json
import tarfile

import pytest

from common_chatbot_image_audit import audit


@pytest.fixture
def images(tmp_path):
    source, repository = "a" * 40, "docker.io/library/anxious-s13-chatbot-api-aaaaaaa"
    build, imported = tmp_path / "build", tmp_path / "imported"
    build.mkdir(); imported.mkdir()
    def encoded(value): return json.dumps(value, sort_keys=True).encode()
    def hashed(data): return "sha256:" + hashlib.sha256(data).hexdigest()
    config = encoded({"architecture": "arm64", "os": "linux", "config": {"Labels": {"org.opencontainers.image.revision": source}}})
    layer = b"synthetic compressed layer bytes"
    manifest = encoded({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json", "config": {"digest": hashed(config)}, "layers": [{"digest": hashed(layer)}]})
    index = encoded({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [{"digest": hashed(manifest), "platform": {"os": "linux", "architecture": "arm64"}}]})
    with tarfile.open(build / "api.tar", "w") as archive:
        for data in (config, layer, manifest, index):
            info = tarfile.TarInfo("blobs/sha256/" + hashed(data).split(":")[1]); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    (build / "api.metadata.json").write_text(json.dumps({"containerimage.digest": hashed(index)}))
    (build / "api.inspect.json").write_text(json.dumps([{"Id": hashed(config)}]))
    for name, data in (("target", index), ("manifest", manifest), ("config", config)):
        (imported / (name + ".json")).write_bytes(data)
    reference = repository + "@" + hashed(index)
    (imported / "cri.json").write_text(json.dumps({"status": {"repoDigests": [reference], "id": hashed(config)}}))
    pod = {"specImage": reference, "criId": hashed(config), "podImageId": "containerd://" + hashed(config)}
    return source, build, imported, reference, pod


def test_archive_import_and_actual_pod_chain_are_bound_to_source(images):
    result = audit("api", *images)
    assert result["podAssociationChecked"] is True and result["nativeStarted"] is False
    assert result["sourceSha"] == images[0] and result["reference"] == images[3]


@pytest.mark.parametrize("damage", ["layer", "source", "manifest", "cri", "pod-spec", "pod-id"])
def test_modified_provenance_or_wrong_pod_refuses(images, damage):
    source, build, imported, reference, pod = images
    if damage == "layer":
        archive = build / "api.tar"
        # Repack a bad layer under its former digest; metadata still claims the original.
        with tarfile.open(archive) as old:
            data = [(entry, old.extractfile(entry).read()) for entry in old]
        with tarfile.open(archive, "w") as new:
            for entry, content in data:
                if content == b"synthetic compressed layer bytes": content = b"tampered layer"; entry.size = len(content)
                new.addfile(entry, io.BytesIO(content))
    elif damage == "source": source = "b" * 40
    elif damage == "manifest": (imported / "manifest.json").write_text('{}')
    elif damage == "cri": (imported / "cri.json").write_text('{"status":{"repoDigests":[],"id":"foreign"}}')
    elif damage == "pod-spec": pod["specImage"] = "unreviewed:tag"
    else: pod["podImageId"] = "containerd://sha256:" + "0" * 64
    with pytest.raises((ValueError, KeyError)): audit("api", source, build, imported, reference, pod)
