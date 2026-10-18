"""Unit tests for Windows firewall / legacy WSL2 checklist generation (K18)."""

from __future__ import annotations

import unittest

from telemetry.checklist import (
    FIREWALL_RULE_NAME,
    LOKI_PORT,
    format_checklist_for_display,
    generate_windows_native_checklist,
    generate_wsl2_nat_checklist,
    refresh_checklist_on_wsl_ip_change,
)
from telemetry.readiness import gateway_loki_ready_curl


class NativeWindowsChecklistTests(unittest.TestCase):
    def test_native_firewall_only_no_portproxy(self) -> None:
        items = generate_windows_native_checklist(monitoring_ip="192.168.1.10")
        by_id = {i.id: i for i in items}
        self.assertIn("firewall_allow", by_id)
        self.assertIn("firewall_show", by_id)
        self.assertIn("user_confirm", by_id)
        self.assertNotIn("portproxy_add", by_id)
        cmds = "\n".join(i.command for i in items if i.command)
        self.assertNotIn("portproxy", cmds.lower())
        self.assertIn("advfirewall", by_id["firewall_allow"].command)
        self.assertIn(FIREWALL_RULE_NAME, by_id["firewall_allow"].command)
        self.assertIn("192.168.1.10:3100/ready", by_id["user_confirm"].notes)
        text = format_checklist_for_display(items)
        self.assertIn("firewall checklist", text.lower())
        self.assertNotIn("wsl2 nat", text.lower())


class ChecklistTests(unittest.TestCase):
    def test_generate_contains_portproxy_and_firewall(self) -> None:
        items = generate_wsl2_nat_checklist(
            wsl2_ip="172.28.123.4",
            monitoring_ip="192.168.1.10",
        )
        by_id = {i.id: i for i in items}
        self.assertIn("portproxy_add", by_id)
        self.assertIn("firewall_allow", by_id)
        self.assertIn("portproxy_show", by_id)
        self.assertIn("user_confirm", by_id)

        proxy = by_id["portproxy_add"].command
        self.assertIn("netsh interface portproxy add v4tov4", proxy)
        self.assertIn("listenport=3100", proxy)
        self.assertIn("connectaddress=172.28.123.4", proxy)
        self.assertIn("connectport=3100", proxy)
        self.assertIn("listenaddress=0.0.0.0", proxy)

        fw = by_id["firewall_allow"].command
        self.assertIn("netsh advfirewall firewall add rule", fw)
        self.assertIn(FIREWALL_RULE_NAME, fw)
        self.assertIn(f"localport={LOKI_PORT}", fw)
        self.assertIn("dir=in", fw)
        self.assertIn("action=allow", fw)

        confirm = by_id["user_confirm"]
        self.assertEqual(confirm.command, "")
        self.assertIn("192.168.1.10:3100/ready", confirm.notes)
        self.assertIn("9137", confirm.notes)

    def test_generate_requires_wsl2_ip(self) -> None:
        with self.assertRaises(ValueError):
            generate_wsl2_nat_checklist(wsl2_ip="  ")

    def test_display_format_is_copyable(self) -> None:
        text = format_checklist_for_display(
            generate_wsl2_nat_checklist(wsl2_ip="172.28.1.1")
        )
        self.assertIn("K18", text)
        self.assertIn("will not", text.lower())
        self.assertIn("netsh interface portproxy", text)
        self.assertIn("netsh advfirewall", text)

    def test_refresh_on_ip_change_prepends_delete_and_firewall(self) -> None:
        items = refresh_checklist_on_wsl_ip_change(
            "172.28.1.1",
            "172.28.9.9",
            monitoring_ip="10.0.0.5",
        )
        ids = [i.id for i in items]
        self.assertEqual(ids[0], "portproxy_delete_stale")
        self.assertIn("firewall_delete", ids)
        self.assertIn("delete v4tov4", items[0].command)
        self.assertIn("172.28.1.1", items[0].notes)
        fw_del = next(i for i in items if i.id == "firewall_delete")
        self.assertIn("delete rule", fw_del.command)
        self.assertIn(FIREWALL_RULE_NAME, fw_del.command)
        add = next(i for i in items if i.id == "portproxy_add")
        self.assertIn("connectaddress=172.28.9.9", add.command)

    def test_only_loki_3100_not_other_ports(self) -> None:
        items = generate_wsl2_nat_checklist(wsl2_ip="172.28.1.1")
        blob = "\n".join(i.command for i in items)
        self.assertNotIn("listenport=9137", blob)
        self.assertNotIn("listenport=8501", blob)
        self.assertNotIn("listenport=9090", blob)

    def test_gateway_loki_ready_curl_helper(self) -> None:
        cmd = gateway_loki_ready_curl("192.168.1.10")
        self.assertEqual(
            cmd,
            "curl -fsS --max-time 5 http://192.168.1.10:3100/ready",
        )


if __name__ == "__main__":
    unittest.main()
