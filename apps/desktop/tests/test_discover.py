"""Unit tests for LAN SSH discovery helpers."""

from __future__ import annotations

import unittest
from unittest import mock

from shell import discover


class DiscoverTests(unittest.TestCase):
    def test_network_caps_large_prefix_at_24(self) -> None:
        net = discover._network_for_scan("192.168.1.20", prefix=16)
        self.assertEqual(str(net), "192.168.1.0/24")

    def test_scan_ssh_hosts_filters_open(self) -> None:
        def fake_probe(ip: str, port: int, timeout_s: float) -> bool:
            return ip.endswith(".21")

        with mock.patch.object(discover, "_probe_tcp", side_effect=fake_probe):
            found = discover.scan_ssh_hosts(
                monitoring_ip="192.168.1.20",
                prefix=30,  # tiny net for speed
                port=22,
                timeout_s=0.01,
            )
        self.assertEqual([h["ip"] for h in found], ["192.168.1.21"])
        self.assertTrue(all(h["open"] and h["port"] == 22 for h in found))


if __name__ == "__main__":
    unittest.main()
