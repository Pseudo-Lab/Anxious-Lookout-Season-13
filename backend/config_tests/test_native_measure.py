"""#68 measurement Pods keep the runner Pod's security settings (pure, no DB)."""
from ops.native_measure import POD_FIELDS, measurement_pod, runner_manifest

IMAGE = 'docker.io/library/anxious-dev-runner@sha256:' + 'b' * 64


def test_security_fields_are_the_runners_field_for_field():
    pod, runner = measurement_pod('v3-selinux', 'native68-measure', IMAGE), runner_manifest(IMAGE)
    for field in POD_FIELDS:
        assert pod['spec'][field] == runner['spec'][field], field
    container, original = pod['spec']['containers'][0], runner['spec']['containers'][0]
    assert container['securityContext'] == original['securityContext']
    assert container['resources'] == original['resources']
    assert container['image'] == IMAGE == original['image']
    assert pod['spec']['securityContext']['seccompProfile'] == {'type': 'Localhost',
                                                               'localhostProfile': 'profiles/anxious-runner-userns-v1.json'}
    assert 'seLinuxOptions' not in pod['spec']['securityContext'] and 'seLinuxOptions' not in container['securityContext']


def test_no_credentials_or_persistent_volumes():
    pod = measurement_pod('v3-selinux', 'native68-measure', IMAGE)
    spec, container = pod['spec'], pod['spec']['containers'][0]
    assert spec['automountServiceAccountToken'] is False and 'serviceAccountName' not in spec
    assert all(set(volume) == {'name', 'emptyDir'} for volume in spec['volumes'])
    assert 'env' not in container and 'envFrom' not in container
    assert container['command'] == ['sleep', 'infinity'] and container['imagePullPolicy'] == 'Never'
    assert [mount['mountPath'] for mount in container['volumeMounts']] == ['/state', '/control', '/tmp']
