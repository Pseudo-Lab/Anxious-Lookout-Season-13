# Pinned Pod schema inventory

`pod-v1.36.4.json` is generated from the official Kubernetes v1.36.4 OpenAPI:
https://raw.githubusercontent.com/kubernetes/kubernetes/v1.36.4/api/openapi-spec/swagger.json

Input SHA256: `dcede2063da1d7ad62ecb5af8adb6d7fabd0b52385a7fa0048afb491dac90450`.
The generator rejects any other bytes, including a changed upstream tag response.
Review an explicit pin update rather than accepting schema drift.

The 113 definitions form the full reference closure of PodSpec and ObjectMeta.
Properties, required fields, types, formats, defaults and Kubernetes list/map
extensions are preserved; descriptions/externalDocs/examples are removed to keep
this an inspectable structural inventory. Maps remain maps, arrays retain their
item schemas, and referenced objects remain typed objects. Do not enumerate typed
CEL objects as maps. Pod status is intentionally outside this inventory; the
policy does not match pods/status. Root Pod apiVersion/kind and AdmissionRequest
are outside these two roots and must be supplied by the later CEL test harness.

This is only the input inventory stage. It does not classify every field as
allowed/absent/defaulted, compile CEL, model feature gates, prove API defaulting,
validate a positive Pod or authorize removing the literal false gate. The next
stage must map Plan plus reviewed defaults against these fields, reject all other
fields, and independently compile/evaluate each validation with positive and
negative cases. ObjectMeta server-owned fields need operation-aware handling;
metadata timestamps/UID/resourceVersion are not user-controlled spec allowances.
K3s actual server-default observations remain separate from this upstream source.

Reproduce with the downloaded schema at `/tmp/issue10-schema/swagger.json` and a
fresh output directory (output files are exclusive-create). Run from repo root:

```sh
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  -v "$PWD/backend:/source:ro" -v /tmp/issue10-schema:/schema:ro \
  -v /tmp/issue10-inventory-output:/output -w /source -e PYTHONPATH=/source \
  --entrypoint python \
  sha256:54b5608ca7caec4a427ae497c00ef2afd9faa38c9ed5e6c845ec35bf7a50805d \
  -B -m ops.runner_schema_inventory --schema /schema/swagger.json \
  --output /output/pod-v1.36.4.json
```

For tests also mount the committed schema directory read-only at `/inventory`:
`-v "$PWD/infra/hosting/m3/user-runner-policy-draft/schema:/inventory:ro"`.
With the same source/schema mounts, `python -B -m pytest -q -p no:cacheprovider
config_tests/test_runner_schema_inventory.py` inside the test image verifies
reference closure, field/required/extension preservation, digest drift refusal
and byte-for-byte equality with the committed inventory (including formatting).
No Kubernetes or host runtime mutation is performed.
