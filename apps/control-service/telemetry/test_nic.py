"""Unit tests for NIC candidate filtering."""

from __future__ import annotations

import unittest

from unittest import mock

from telemetry.nic import (
    NicCandidate,
    filter_nic_candidates,
    is_wsl_nat_iface,
    list_ipv4_candidates,
    name_excluded,
    pick_default_monitoring_ip,
)


def _c(name: str, ip: str, prefix: int | None = 24) -> NicCandidate:
    return NicCandidate(name=name, ip=ip, prefix=prefix)


class NicFilterTests(unittest.TestCase):
    def test_excludes_docker0_and_bridge(self) -> None:
        raw = [
            _c("eth0", "192.168.1.10"),
            _c("docker0", "172.17.0.1"),
            _c("br-abcd1234", "172.18.0.1"),
            _c("veth0abc", "169.254.1.1"),
            _c("lo", "127.0.0.1", 8),
        ]
        got = filter_nic_candidates(raw)
        self.assertEqual([c.name for c in got], ["eth0"])
        self.assertEqual(got[0].ip, "192.168.1.10")

    def test_excludes_wsl_vethernet_by_name(self) -> None:
        raw = [
            _c("Ethernet", "192.168.40.20"),
            _c("vEthernet (WSL)", "172.28.0.1"),
            _c("vEthernet (WSL (Hyper-V firewall))", "172.29.0.1"),
            _c("vEthernet (Default Switch)", "172.27.0.1"),
            _c("Wi-Fi", "10.0.0.5"),
        ]
        got = filter_nic_candidates(raw)
        names = [c.name for c in got]
        self.assertEqual(names, ["Ethernet", "Wi-Fi"])

    def test_keeps_external_vethernet_lan(self) -> None:
        # Case-insensitive ^veth must NOT swallow Hyper-V external LAN adapters.
        raw = [
            _c("vEthernet (External Switch)", "192.168.40.20"),
            _c("vEthernet (LAN)", "10.0.0.8"),
            _c("veth0abc", "172.17.0.2"),  # Linux pair — still excluded
        ]
        got = filter_nic_candidates(raw)
        self.assertEqual(
            [c.name for c in got],
            ["vEthernet (External Switch)", "vEthernet (LAN)"],
        )

    def test_linux_veth_case_sensitive(self) -> None:
        self.assertTrue(name_excluded("veth0abc"))
        self.assertFalse(name_excluded("vEthernet (External Switch)"))
        self.assertFalse(name_excluded("VETH0"))  # not a Linux veth pair name

    def test_excludes_loopback_and_link_local_ips(self) -> None:
        raw = [
            _c("eth0", "127.0.0.2"),
            _c("eth0", "169.254.10.10"),
            _c("eth0", "192.168.1.50"),
        ]
        got = filter_nic_candidates(raw)
        self.assertEqual([c.ip for c in got], ["192.168.1.50"])

    def test_can_keep_docker_when_flag_false(self) -> None:
        raw = [_c("docker0", "172.17.0.1"), _c("eth0", "192.168.1.10")]
        got = filter_nic_candidates(
            raw, exclude_docker_wsl=False, exclude_link_local=False
        )
        self.assertEqual([c.name for c in got], ["docker0", "eth0"])

    def test_wsl_nat_eth0_172_filtered_when_flag_set(self) -> None:
        # Repro from review: eth0 172.28.x must not be MONITORING_IP on NAT path.
        raw = [
            _c("eth0", "172.28.123.4", 20),
            _c("Ethernet", "192.168.1.10"),
        ]
        kept_bare = filter_nic_candidates(raw)  # bare metal: keep eth0 172.x
        self.assertEqual([c.ip for c in kept_bare], ["172.28.123.4", "192.168.1.10"])

        got = filter_nic_candidates(raw, exclude_wsl_nat=True)
        self.assertEqual([c.ip for c in got], ["192.168.1.10"])
        self.assertTrue(is_wsl_nat_iface(_c("eth0", "172.28.123.4", 20)))
        # Bare-metal corporate 172.16 on enp* is not treated as WSL NAT.
        corp = _c("enp1s0", "172.16.5.10", 16)
        self.assertFalse(is_wsl_nat_iface(corp))
        self.assertEqual(
            filter_nic_candidates([corp], exclude_wsl_nat=True),
            [corp],
        )

    def test_name_excluded_helpers(self) -> None:
        self.assertTrue(name_excluded("docker0"))
        self.assertTrue(name_excluded("br-deadbeef"))
        self.assertTrue(name_excluded("vEthernet (WSL)"))
        self.assertFalse(name_excluded("enp0s3"))
        self.assertFalse(name_excluded("Ethernet 2"))

    def test_pick_default_prefers_gateway_subnet(self) -> None:
        cands = [
            _c("wlan0", "10.0.0.5", 24),
            _c("eth0", "192.168.40.20", 24),
        ]
        pick = pick_default_monitoring_ip(cands, gateway_ip="192.168.40.177")
        assert pick is not None
        self.assertEqual(pick.name, "eth0")

    def test_pick_default_falls_back_to_first(self) -> None:
        cands = [_c("wlan0", "10.0.0.5", 24)]
        pick = pick_default_monitoring_ip(cands, gateway_ip="192.168.1.1")
        assert pick is not None
        self.assertEqual(pick.ip, "10.0.0.5")

    def test_native_windows_uses_powershell_nics(self) -> None:
        host = [_c("Ethernet", "192.168.40.20"), _c("vEthernet (WSL)", "172.28.0.1")]
        with (
            mock.patch("telemetry.nic.os.name", "nt"),
            mock.patch("telemetry.nic.in_wsl", return_value=False),
            mock.patch(
                "telemetry.nic._collect_windows_host_candidates", return_value=host
            ),
            mock.patch(
                "telemetry.nic._collect_linux_candidates",
                side_effect=AssertionError("linux ip must not run on native Windows"),
            ),
        ):
            got = list_ipv4_candidates()
        self.assertEqual([c.name for c in got], ["Ethernet"])

    def test_bare_linux_ignores_windows_net_mode(self) -> None:
        linux = [_c("enp0s3", "192.168.1.50")]
        with (
            mock.patch("telemetry.nic.os.name", "posix"),
            mock.patch("telemetry.nic.in_wsl", return_value=False),
            mock.patch(
                "telemetry.nic._collect_linux_candidates", return_value=linux
            ),
            mock.patch(
                "telemetry.nic._collect_windows_host_candidates",
                side_effect=AssertionError("powershell must not run on bare Linux"),
            ),
        ):
            got = list_ipv4_candidates(windows_net_mode="nat")
        self.assertEqual([c.ip for c in got], ["192.168.1.50"])

    def test_pick_default_empty(self) -> None:
        self.assertIsNone(pick_default_monitoring_ip([]))


if __name__ == "__main__":
    unittest.main()
