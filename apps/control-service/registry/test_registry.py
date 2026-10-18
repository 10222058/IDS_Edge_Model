"""Unit tests for Registry (tempfile SQLite)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from registry import Registry


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "registry.sqlite")
        self.reg = Registry(self.db_path)

    def tearDown(self) -> None:
        self.reg.close()
        self._tmpdir.cleanup()

    def _seed_gateway(self, gid: str = "gw1") -> dict:
        return self.reg.upsert_gateway(
            id=gid,
            host="192.168.1.10",
            ssh_user="pi",
            install_root="/opt/iot-gateway",
            fingerprint="SHA256:abc",
            monitoring_ip="192.168.1.20",
            agent_url="http://192.168.1.10:9138",
            status="online",
        )

    def test_upsert_and_get_gateway(self) -> None:
        g = self._seed_gateway()
        self.assertEqual(g["id"], "gw1")
        self.assertEqual(g["host"], "192.168.1.10")
        self.assertEqual(g["password_auth_enabled"], 0)
        got = self.reg.get_gateway("gw1")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["fingerprint"], "SHA256:abc")

    def test_upsert_gateway_idempotent_preserves_created_at(self) -> None:
        g1 = self._seed_gateway()
        created = g1["created_at"]
        g2 = self.reg.upsert_gateway(
            id="gw1",
            host="192.168.1.11",
            ssh_user="ubuntu",
            install_root="/opt/iot-gateway",
            fingerprint="SHA256:def",
            last_seen_at=created + 100,
            status="degraded",
        )
        self.assertEqual(g2["host"], "192.168.1.11")
        self.assertEqual(g2["ssh_user"], "ubuntu")
        self.assertEqual(g2["created_at"], created)
        self.assertEqual(g2["last_seen_at"], created + 100)
        self.assertEqual(len(self.reg.list_gateways()), 1)

    def test_upsert_gateway_partial_preserves_optionals(self) -> None:
        g1 = self._seed_gateway()
        seen = g1["last_seen_at"]
        g2 = self.reg.upsert_gateway(
            id="gw1",
            host="192.168.1.10",
            ssh_user="pi",
            install_root="/opt/iot-gateway",
            fingerprint="SHA256:abc",
        )
        self.assertEqual(g2["monitoring_ip"], "192.168.1.20")
        self.assertEqual(g2["agent_url"], "http://192.168.1.10:9138")
        self.assertEqual(g2["status"], "online")
        self.assertEqual(g2["last_seen_at"], seen)
        g3 = self.reg.upsert_gateway(
            id="gw1",
            host="192.168.1.10",
            ssh_user="pi",
            install_root="/opt/iot-gateway",
            fingerprint="SHA256:abc",
            monitoring_ip=None,
            status="offline",
        )
        self.assertIsNone(g3["monitoring_ip"])
        self.assertEqual(g3["agent_url"], "http://192.168.1.10:9138")
        self.assertEqual(g3["status"], "offline")
        self.assertEqual(g3["last_seen_at"], seen)

    def test_list_gateways(self) -> None:
        self._seed_gateway("gw1")
        self._seed_gateway("gw2")
        ids = [g["id"] for g in self.reg.list_gateways()]
        self.assertEqual(ids, ["gw1", "gw2"])

    def test_upsert_device_and_list_filter(self) -> None:
        self._seed_gateway()
        self.reg.upsert_device(
            "gw1",
            "sensor1",
            ip="10.0.0.5",
            topics_rw=["sensors/sensor1/#"],
            topics_r=["broadcast/#"],
        )
        self.reg.upsert_device(
            "gw1",
            "sensor2",
            ip="10.0.0.6",
            monitor_enabled=0,
        )
        all_devs = self.reg.list_devices("gw1")
        self.assertEqual(len(all_devs), 2)
        monitored = self.reg.list_devices("gw1", monitor_enabled=1)
        self.assertEqual([d["id"] for d in monitored], ["sensor1"])
        d = self.reg.get_device("gw1", "sensor1")
        assert d is not None
        self.assertEqual(json.loads(d["topics_rw"]), ["sensors/sensor1/#"])

    def test_upsert_device_idempotent(self) -> None:
        self._seed_gateway()
        d1 = self.reg.upsert_device("gw1", "sensor1", ip="10.0.0.5")
        created = d1["created_at"]
        d2 = self.reg.upsert_device(
            "gw1",
            "sensor1",
            ip="10.0.0.9",
            topics_rw=["x/#"],
            cert_fingerprint="fp1",
        )
        self.assertEqual(d2["ip"], "10.0.0.9")
        self.assertEqual(d2["created_at"], created)
        self.assertEqual(d2["cert_fingerprint"], "fp1")
        self.assertEqual(len(self.reg.list_devices("gw1")), 1)

    def test_upsert_device_partial_preserves_ip_and_cert(self) -> None:
        """Regression: topics-only upsert must not clear ip / cert_fingerprint."""
        self._seed_gateway()
        self.reg.upsert_device(
            "gw1",
            "sensor1",
            ip="10.0.0.5",
            topics_rw=["old/#"],
            topics_r=["r/#"],
            cert_fingerprint="fp-keep",
            cert_expires_at=9999,
            meta_json={"k": "v"},
            monitor_enabled=1,
        )
        d2 = self.reg.upsert_device(
            "gw1",
            "sensor1",
            topics_rw=["new/#"],
        )
        self.assertEqual(d2["ip"], "10.0.0.5")
        self.assertEqual(d2["cert_fingerprint"], "fp-keep")
        self.assertEqual(d2["cert_expires_at"], 9999)
        self.assertEqual(json.loads(d2["topics_r"]), ["r/#"])
        self.assertEqual(json.loads(d2["meta_json"]), {"k": "v"})
        self.assertEqual(d2["monitor_enabled"], 1)
        self.assertEqual(json.loads(d2["topics_rw"]), ["new/#"])
        # Explicit None clears a nullable column
        d3 = self.reg.upsert_device("gw1", "sensor1", ip=None)
        self.assertIsNone(d3["ip"])
        self.assertEqual(d3["cert_fingerprint"], "fp-keep")

    def test_delete_gateway_cascades_devices(self) -> None:
        self._seed_gateway("gw1")
        self.reg.upsert_device("gw1", "sensor1", ip="10.0.0.5")
        self.reg.import_allowlist_ips("gw1", ["10.0.0.5"])
        self.assertTrue(self.reg.delete_gateway("gw1"))
        self.assertIsNone(self.reg.get_gateway("gw1"))
        self.assertEqual(self.reg.list_devices("gw1"), [])
        self.assertFalse(self.reg.delete_gateway("gw1"))

    def test_delete_device(self) -> None:
        self._seed_gateway()
        self.reg.upsert_device("gw1", "sensor1", ip="10.0.0.5")
        self.reg.link_allowlist_ip("gw1", "10.0.0.5", "sensor1")
        self.assertTrue(self.reg.delete_device("gw1", "sensor1"))
        self.assertIsNone(self.reg.get_device("gw1", "sensor1"))
        self.assertFalse(self.reg.delete_device("gw1", "sensor1"))
        linked = self.reg.list_allowlist_ips("gw1")
        self.assertEqual(linked[0]["linked_device_id"], None)

    def test_import_allowlist_ips_idempotent(self) -> None:
        self._seed_gateway()
        self.reg.upsert_device("gw1", "sensor1")
        n1 = self.reg.import_allowlist_ips("gw1", ["10.0.0.1", "10.0.0.2", ""])
        self.assertEqual(n1, 2)
        self.reg.link_allowlist_ip("gw1", "10.0.0.1", "sensor1")
        n2 = self.reg.import_allowlist_ips("gw1", ["10.0.0.1", "10.0.0.3"])
        self.assertEqual(n2, 1)
        rows = {r["ip"]: r for r in self.reg.list_allowlist_ips("gw1")}
        self.assertEqual(rows["10.0.0.1"]["linked_device_id"], "sensor1")
        self.assertIsNone(rows["10.0.0.2"]["linked_device_id"])
        self.assertIsNone(rows["10.0.0.3"]["linked_device_id"])

    def test_import_allowlist_requires_gateway(self) -> None:
        with self.assertRaises(Exception):
            self.reg.import_allowlist_ips("missing-gw", ["10.0.0.1"])

    def test_import_acl_usernames_no_overwrite(self) -> None:
        self._seed_gateway()
        self.reg.upsert_device("gw1", "sensor1", ip="10.0.0.5")
        n = self.reg.import_acl_usernames(
            "gw1",
            ["sensor1", "sensor2", "sensor3"],
            topics_rw=["sensors/+/data"],
        )
        self.assertEqual(n, 2)
        d1 = self.reg.get_device("gw1", "sensor1")
        assert d1 is not None
        self.assertEqual(d1["ip"], "10.0.0.5")
        d2 = self.reg.get_device("gw1", "sensor2")
        assert d2 is not None
        self.assertIsNone(d2["ip"])
        self.assertEqual(json.loads(d2["topics_rw"]), ["sensors/+/data"])
        self.assertEqual(
            self.reg.import_acl_usernames("gw1", ["sensor2", "sensor3"]), 0
        )

    def test_link_allowlist_ip(self) -> None:
        self._seed_gateway()
        self.reg.upsert_device("gw1", "sensor9")
        self.reg.import_allowlist_ips("gw1", ["10.0.0.8"])
        row = self.reg.link_allowlist_ip("gw1", "10.0.0.8", "sensor9")
        self.assertEqual(row["linked_device_id"], "sensor9")
        row2 = self.reg.link_allowlist_ip("gw1", "10.0.0.9", "sensor9")
        self.assertEqual(row2["ip"], "10.0.0.9")

    def test_link_allowlist_requires_device(self) -> None:
        self._seed_gateway()
        with self.assertRaises(ValueError):
            self.reg.link_allowlist_ip("gw1", "10.0.0.8", "missing-device")

    def test_audit_and_list(self) -> None:
        self._seed_gateway()
        a1 = self.reg.audit(
            "register_device",
            gateway_id="gw1",
            device_id="sensor1",
            detail={"ip": "10.0.0.5"},
            actor="admin",
        )
        self.assertEqual(a1["action"], "register_device")
        self.assertIn("10.0.0.5", a1["detail_json"] or "")
        self.assertIn("ts", a1)
        self.reg.audit("clear_logs", gateway_id="gw1", actor="admin")
        self.reg.audit("other", gateway_id="gw2")
        rows = self.reg.list_audit(gateway_id="gw1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action"], "clear_logs")
        all_rows = self.reg.list_audit(limit=10)
        self.assertEqual(len(all_rows), 3)

    def test_foreign_key_device_requires_gateway(self) -> None:
        with self.assertRaises(Exception):
            self.reg.upsert_device("missing-gw", "sensor1")


if __name__ == "__main__":
    unittest.main()
