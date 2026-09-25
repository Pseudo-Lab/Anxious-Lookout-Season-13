"""Pure mocked safety checks; execute in a PM-managed container, not on host."""
import copy
import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).resolve().parents[1] / "host/firewall-sync.py"
spec = importlib.util.spec_from_file_location("firewall_sync", path)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def pod(namespace="kube-system", account="coredns", address="10.42.0.20", **extra):
    value = {"kind": "Pod", "metadata": {"namespace": namespace},
             "spec": {"serviceAccountName": account},
             "status": {"phase": "Running", "podIP": address}}
    value["spec"].update(extra)
    return value


class SelectionTests(unittest.TestCase):
    def test_only_current_trusted_system_accounts(self):
        items = [pod(), pod(account="metrics-server", address="10.42.0.21"),
                 pod(namespace="tenant-validation-a", account="coredns", address="10.42.0.22"),
                 pod(account="default", address="10.42.0.23"),
                 pod(hostNetwork=True, address="10.42.0.24"),
                 pod(address="172.18.0.2"), pod(address="fd00::1")]
        terminating = pod(address="10.42.0.25")
        terminating["metadata"]["deletionTimestamp"] = "2026-09-25T00:00:00Z"
        items.append(terminating)
        completed = pod(address="10.42.0.26")
        completed["status"]["phase"] = "Succeeded"
        items.append(completed)
        for kind in ("List", "PodList"):
            desired = sync.desired_sets({"kind": kind, "items": items})
            self.assertEqual(desired["anxious-k3s-api-clients"], {"10.42.0.20", "10.42.0.21"})
            self.assertEqual(desired["anxious-k3s-metrics-clients"], {"10.42.0.21"})

    def test_api_failure_does_not_touch_firewall(self):
        called = []
        def unavailable(_):
            raise RuntimeError("API unavailable")
        old_command, old_firewall = sync.command, sync.firewall
        try:
            sync.command = unavailable
            sync.firewall = lambda *args: called.append(args)
            with self.assertRaises(RuntimeError):
                sync.reconcile()
            self.assertEqual(called, [])
        finally:
            sync.command, sync.firewall = old_command, old_firewall

    def test_missing_identity_guard_does_not_touch_firewall(self):
        called = []
        old_command, old_firewall = sync.command, sync.firewall
        try:
            sync.command = lambda _: '{"spec": {}}'
            sync.firewall = lambda *args: called.append(args)
            with self.assertRaises(ValueError):
                sync.reconcile()
            self.assertEqual(called, [])
        finally:
            sync.command, sync.firewall = old_command, old_firewall


if __name__ == "__main__":
    unittest.main()
