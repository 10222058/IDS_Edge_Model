"""Smoke the Setup Wizard HTTP API (no live SSH / Docker daemon required)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from shell.wizard_server import serve


class WizardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.env = mock.patch.dict(os.environ, {"IOTGW_DATA_DIR": self._td.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Bind ephemeral port
        self.server = serve("127.0.0.1", 0, open_browser=False)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_env_and_static(self) -> None:
        env = self._get("/api/wizard/env")
        self.assertIn("telemetry", env)
        self.assertEqual(env["telemetry"].get("backend"), "native")
        self.assertFalse(env.get("docker_required", True))
        self.assertIn("setup_complete", env)
        self.assertIn(env.get("entry"), ("wizard", "app"))
        with urllib.request.urlopen(self.base + "/wizard.html", timeout=5) as resp:
            wizard_html = resp.read().decode("utf-8")
        self.assertIn("IoT Gateway Monitor", wizard_html)
        self.assertIn("Install monitoring", wizard_html)
        self.assertIn("Open Monitoring", wizard_html)
        self.assertNotIn("Full setup", wizard_html)
        self.assertNotIn("Monitoring only", wizard_html)
        self.assertNotIn("uninstall-modal", wizard_html)
        self.assertNotIn("btn-uninstall", wizard_html)
        self.assertIn("first-run", wizard_html)
        self.assertIn("app-shell.js", wizard_html)
        self.assertNotIn('href="actions.html"', wizard_html)
        self.assertNotIn('href="monitoring.html"', wizard_html)
        self.assertNotIn('href="admin.html"', wizard_html)
        self.assertNotIn("lab.html", wizard_html)
        with urllib.request.urlopen(self.base + "/gateways.html", timeout=5) as resp:
            gw_html = resp.read().decode("utf-8")
        self.assertIn("Registration", gw_html)
        self.assertIn("Clean up", gw_html)
        self.assertIn("gateways.js", gw_html)
        self.assertIn("app-shell.js", gw_html)
        with urllib.request.urlopen(self.base + "/settings.html", timeout=5) as resp:
            settings_html = resp.read().decode("utf-8")
        self.assertIn("Remove", settings_html)
        self.assertIn("monitoring panel", settings_html.lower())
        self.assertIn("Loki and Prometheus", settings_html)
        self.assertIn("Local app data", settings_html)
        self.assertNotIn("Gateway only", settings_html)
        self.assertNotIn("Gateways → Clean up", settings_html)
        self.assertIn("settings.js", settings_html)
        with urllib.request.urlopen(self.base + "/actions.html", timeout=5) as resp:
            actions_html = resp.read().decode("utf-8")
        self.assertIn("Control Plane", actions_html)
        self.assertIn("Rotate CA", actions_html)
        self.assertIn("app-shell.js", actions_html)
        with urllib.request.urlopen(self.base + "/monitoring.html", timeout=5) as resp:
            mon = resp.read().decode("utf-8")
        self.assertIn("Monitoring", mon)
        self.assertIn("Gateway Metric", mon)
        self.assertIn("monitoring.js", mon)
        self.assertIn("app-shell.js", mon)
        with urllib.request.urlopen(self.base + "/app-shell.js", timeout=5) as resp:
            shell = resp.read().decode("utf-8")
        self.assertIn("Monitoring", shell)
        self.assertIn("Gateways", shell)
        self.assertIn("Registration", shell)
        self.assertIn("Clean up", shell)
        self.assertIn("Settings", shell)
        self.assertIn("gateways.html", shell)
        self.assertIn("settings.html", shell)
        self.assertIn("Remove", shell)
        self.assertNotIn("wizard.html", shell)
        with urllib.request.urlopen(self.base + "/wizard.js", timeout=5) as resp:
            wjs = resp.read().decode("utf-8")
        self.assertIn("background: true", wjs)
        self.assertNotIn("iotgw_setup_path", wjs)
        self.assertNotIn("/api/wizard/uninstall", wjs)
        self.assertIn("/api/wizard/setup/complete", wjs)
        with urllib.request.urlopen(self.base + "/settings.js", timeout=5) as resp:
            sjs = resp.read().decode("utf-8")
        self.assertIn("/api/wizard/uninstall", sjs)
        self.assertIn("monitoring: true", sjs)
        with urllib.request.urlopen(self.base + "/gateways.js", timeout=5) as resp:
            gjs = resp.read().decode("utf-8")
        self.assertIn("/api/wizard/provision", gjs)
        self.assertIn("monitoring: false", gjs)
        self.assertIn("/api/wizard/settings/gateway", gjs)
        self.assertIn("/api/wizard/discover/gateways", gjs)
        self.assertIn("/api/wizard/firewall/apply", gjs)
        self.assertNotIn("checklist-wrap", gjs)
        with urllib.request.urlopen(self.base + "/gateways.html", timeout=5) as resp:
            gw_html2 = resp.read().decode("utf-8")
        self.assertIn("Scan LAN for SSH", gw_html2)
        self.assertIn("Apply firewall", gw_html2)
        self.assertNotIn("checklist-wrap", gw_html2)
        self.assertNotIn('id="gateway-ip"', gw_html2)
        with urllib.request.urlopen(self.base + "/monitoring.js", timeout=5) as resp:
            js = resp.read().decode("utf-8")
        self.assertIn("/api/v1/query/summaries", js)
        self.assertIn("Gateway Metric", js)
        with urllib.request.urlopen(self.base + "/splash.html", timeout=5) as resp:
            splash = resp.read().decode("utf-8")
        self.assertIn("setup_complete", splash)
        self.assertIn("monitoring.html#metric", splash)

    def test_setup_complete_marks_entry_app(self) -> None:
        env = self._get("/api/wizard/env")
        self.assertFalse(env.get("setup_complete"))
        self.assertEqual(env.get("entry"), "wizard")
        out = self._post("/api/wizard/setup/complete", {})
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("setup_complete"))
        env2 = self._get("/api/wizard/env")
        self.assertTrue(env2.get("setup_complete"))
        self.assertEqual(env2.get("entry"), "app")

    def test_settings_gateway_persists_ssh(self) -> None:
        from shell.paths import load_settings

        out = self._post(
            "/api/wizard/settings/gateway",
            {
                "gateway_id": "gw-lab",
                "gateway_ip": "192.168.1.60",
                "ssh_host": "192.168.1.60",
                "ssh_user": "pi",
                "ssh_port": 2222,
            },
        )
        self.assertTrue(out.get("ok"))
        s = load_settings()
        self.assertEqual(s.get("default_gateway_id"), "gw-lab")
        self.assertEqual(s.get("ssh_host"), "192.168.1.60")
        self.assertEqual(s.get("ssh_user"), "pi")
        self.assertEqual(s.get("ssh_port"), 2222)

    def test_control_config(self) -> None:
        data = self._get("/api/wizard/control")
        self.assertIn("url", data)
        self.assertTrue(data["url"].startswith("http://127.0.0.1:"))
        self.assertNotIn("actions_path", data)

    def test_checklist_windows_native_no_portproxy(self) -> None:
        from unittest import mock

        from shell.paths import load_settings, save_settings

        s = load_settings()
        s["monitoring_ip"] = "192.168.1.20"
        save_settings(s)
        with mock.patch("shell.detect.operator_runtime", return_value="windows"):
            data = self._get("/api/wizard/checklist")
        self.assertTrue(data.get("applicable"))
        self.assertEqual(data.get("path"), "windows_native")
        self.assertTrue(data.get("auto_apply"))
        cmds = [i["command"] for i in data["items"] if i.get("command")]
        self.assertTrue(any("advfirewall" in c for c in cmds))
        self.assertFalse(any("portproxy" in c for c in cmds))
        conf = self._post("/api/wizard/checklist/confirm", {})
        self.assertTrue(conf["confirmed"])

    def test_discover_gateways_requires_monitoring_ip(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/wizard/discover/gateways",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_discover_gateways_returns_hosts(self) -> None:
        from unittest import mock

        from shell.paths import save_settings

        save_settings({"monitoring_ip": "192.168.1.20"})
        with mock.patch(
            "shell.discover.scan_ssh_hosts",
            return_value=[
                {"ip": "192.168.1.50", "port": 22, "open": True},
                {"ip": "192.168.1.60", "port": 22, "open": True},
            ],
        ):
            data = self._post("/api/wizard/discover/gateways", {})
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("count"), 2)
        ips = {h["ip"] for h in data.get("hosts") or []}
        self.assertEqual(ips, {"192.168.1.50", "192.168.1.60"})

    def test_firewall_apply_skips_non_windows(self) -> None:
        from unittest import mock

        with mock.patch("shell.detect.operator_runtime", return_value="linux"):
            with mock.patch("shell.firewall_apply.is_elevated", return_value=None):
                data = self._post("/api/wizard/firewall/apply", {})
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("skipped"))
        self.assertFalse(data.get("applicable"))

    def test_firewall_apply_requires_admin_on_windows(self) -> None:
        from unittest import mock

        with mock.patch("shell.detect.operator_runtime", return_value="windows"):
            with mock.patch("shell.firewall_apply.is_elevated", return_value=False):
                req = urllib.request.Request(
                    self.base + "/api/wizard/firewall/apply",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_nic_select_persists(self) -> None:
        out = self._post(
            "/api/wizard/nics/select",
            {"monitoring_ip": "10.0.0.5", "nic_name": "eth0"},
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["settings"]["monitoring_ip"], "10.0.0.5")

    def test_install_key_rejects_without_pin(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/wizard/ssh/install-key",
            data=json.dumps(
                {
                    "host": "10.0.0.9",
                    "username": "ubuntu",
                    "password": "secret",
                    "gateway_id": "gw",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("pin", body.get("error", "").lower())

    def test_telemetry_ensure_background_job(self) -> None:
        from shell import wizard_server as ws

        class _Snap:
            ready = False
            binaries_present = True
            loki_running = False
            prometheus_running = False
            detail = "waiting"
            progress = "Downloading (10%)"

        class _FakeTel:
            def snapshot(self):
                return _Snap()

            def ensure_binaries(self, force=False):  # noqa: ANN001
                return {}

            def start(self, **kwargs):  # noqa: ANN003
                import time

                time.sleep(0.15)

                class _St:
                    running = True
                    detail = "Loki and Prometheus are running."
                    loki = None
                    prometheus = None

                _Snap.ready = True
                _Snap.loki_running = True
                _Snap.prometheus_running = True
                _Snap.detail = "Loki and Prometheus are running."
                return _St()

        with mock.patch.object(ws.STATE, "_telemetry", _FakeTel()):
            # Reset job state between tests (module-level STATE).
            with ws.STATE.lock:
                ws.STATE._telemetry_job = {
                    "state": "idle",
                    "detail": "",
                    "error": None,
                }
            out = self._post(
                "/api/wizard/telemetry/ensure",
                {"background": True},
            )
            self.assertTrue(out.get("background"))
            self.assertIn(out.get("job", {}).get("state"), ("running", "ok"))

            import time

            deadline = time.time() + 5
            final = None
            while time.time() < deadline:
                final = self._get("/api/wizard/telemetry/status")
                if final.get("job", {}).get("state") in ("ok", "error"):
                    break
                time.sleep(0.05)
            self.assertIsNotNone(final)
            assert final is not None
            self.assertEqual(final["job"]["state"], "ok")
            self.assertTrue(final.get("ready"))

    def test_services_start_default_skips_streamlit(self) -> None:
        from shell import wizard_server as ws

        calls: list[str] = []

        class _FakeMgr:
            def start_control_service(self, **kwargs):  # noqa: ANN003
                calls.append("control")

                class _P:
                    url = "http://127.0.0.1:9137"

                return _P()

            def start_streamlit(self, **kwargs):  # noqa: ANN003
                calls.append("streamlit")

                class _P:
                    url = "http://127.0.0.1:8501"

                return _P()

            def status(self):
                return {
                    "control": {"running": True},
                    "streamlit": {"running": False},
                }

        with mock.patch.object(ws.STATE, "manager", _FakeMgr()):
            with mock.patch.object(ws, "wait_http", return_value=True):
                out = self._post("/api/wizard/services/start", {})
        self.assertIn("control", calls)
        self.assertNotIn("streamlit", calls)
        self.assertTrue(out["control"]["healthy"])
        self.assertIsNone(out["streamlit"]["url"])

        calls.clear()
        with mock.patch.object(ws.STATE, "manager", _FakeMgr()):
            with mock.patch.object(ws, "wait_http", return_value=True):
                out2 = self._post("/api/wizard/services/start", {"streamlit": True})
        self.assertIn("streamlit", calls)
        self.assertEqual(out2["streamlit"]["url"], "http://127.0.0.1:8501")

    def test_open_streamlit_requires_running(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/wizard/open-streamlit",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_proxy_control_injects_api_token(self) -> None:
        from shell import wizard_server as ws

        captured: dict = {}
        real_urlopen = urllib.request.urlopen

        class _Resp:
            status = 200
            headers = {"Content-Type": "application/json"}

            def read(self) -> bytes:
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        def fake_urlopen(req, timeout=30):  # noqa: ANN001
            url = req if isinstance(req, str) else req.full_url
            # Wizard→control proxy only; pass through the test→wizard request.
            if ":9137" not in url:
                return real_urlopen(req, timeout=timeout)
            captured["url"] = url
            captured["headers"] = dict(req.headers)
            captured["method"] = req.get_method()
            return _Resp()

        with mock.patch.dict(os.environ, {"IOTGW_API_TOKEN": "secret-token"}):
            with mock.patch.object(ws.urllib.request, "urlopen", fake_urlopen):
                with urllib.request.urlopen(
                    self.base + "/api/v1/gateways", timeout=5
                ) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body, {"ok": True})
        self.assertIn("http://127.0.0.1:9137/api/v1/gateways", captured["url"])
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers.get("x-api-token"), "secret-token")

    def test_uninstall_monitoring_without_gateway(self) -> None:
        from pathlib import Path

        from shell import wizard_server as ws
        from shell.paths import data_dir

        tel_root = Path(self._td.name) / "telemetry"
        tel_root.mkdir(parents=True)
        (tel_root / "bin").mkdir()
        (tel_root / "bin" / "loki").write_text("x", encoding="utf-8")
        (Path(self._td.name) / "settings.json").write_text(
            '{"monitoring_ip":"192.168.1.20"}', encoding="utf-8"
        )

        class _FakeTel:
            def stop(self):
                return None

            def uninstall(self):
                import shutil

                if tel_root.is_dir():
                    shutil.rmtree(tel_root)
                return {"ok": True, "root": str(tel_root), "removed": ["bin"], "errors": []}

        class _FakeMgr:
            def stop_all(self):
                return None

        with mock.patch.object(ws.STATE, "_telemetry", _FakeTel()):
            with mock.patch.object(ws.STATE, "manager", _FakeMgr()):
                out = self._post(
                    "/api/wizard/uninstall",
                    {"monitoring": True, "gateway": True},
                )
        self.assertTrue(out.get("ok"))
        self.assertIsNotNone(out.get("monitoring"))
        self.assertIsNotNone(out.get("app_data"))
        self.assertFalse(tel_root.exists())
        # wipe recreates empty data dir
        self.assertTrue(data_dir().is_dir())

    def test_uninstall_monitoring_forces_all_gateways(self) -> None:
        """Monitoring wipe auto-includes every registered gateway (needs sudo)."""
        from pathlib import Path

        from shell.paths import save_settings

        save_settings(
            {
                "ssh_host": "192.168.1.50",
                "ssh_user": "ubuntu",
                "gateway_ip": "192.168.1.50",
                "default_gateway_id": "gateway",
            }
        )
        from shell import uninstall as uninstall_mod

        # monitoring=True with gateway=False still couples and needs sudo for SSH.
        with self.assertRaises(uninstall_mod.UninstallError) as ctx:
            uninstall_mod.run_uninstall(
                gateway=False,
                monitoring=True,
                stop_telemetry=lambda: None,
                stop_services=lambda: None,
                telemetry_uninstall=lambda: {"ok": True},
            )
        self.assertIn("sudo password", str(ctx.exception).lower())

        # API: monitoring alone → gateway forced → 409 without sudo.
        req = urllib.request.Request(
            self.base + "/api/wizard/uninstall",
            data=json.dumps({"monitoring": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as http_ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(http_ctx.exception.code, 409)
        self.assertTrue(Path(self._td.name).is_dir())

    def test_uninstall_targets_lists_registry_gateways(self) -> None:
        from registry.registry import Registry
        from shell.paths import ensure_data_dir

        reg = Registry(str(ensure_data_dir() / "registry.sqlite"))
        try:
            reg.upsert_gateway(
                "gw-a",
                host="192.168.1.10",
                ssh_user="ubuntu",
                install_root="/opt/iot-gateway-agent",
                fingerprint="aa",
            )
            reg.upsert_gateway(
                "gw-b",
                host="192.168.1.11",
                ssh_user="ubuntu",
                install_root="/opt/iot-gateway-agent",
                fingerprint="bb",
            )
        finally:
            reg.close()
        data = self._get("/api/wizard/uninstall/targets")
        ids = {g["id"] for g in data.get("gateways") or []}
        self.assertIn("gw-a", ids)
        self.assertIn("gw-b", ids)
        self.assertIn("monitoring_hint", data)

    def test_uninstall_monitoring_removes_all_registry_gateways(self) -> None:
        from pathlib import Path
        from unittest import mock

        from registry.registry import Registry
        from shell import uninstall as uninstall_mod
        from shell.paths import ensure_data_dir

        reg_path = ensure_data_dir() / "registry.sqlite"
        reg = Registry(str(reg_path))
        try:
            reg.upsert_gateway(
                "gw-a",
                host="192.168.1.10",
                ssh_user="ubuntu",
                install_root="/opt/iot-gateway-agent",
                fingerprint="aa",
            )
            reg.upsert_gateway(
                "gw-b",
                host="192.168.1.11",
                ssh_user="ubuntu",
                install_root="/opt/iot-gateway-agent",
                fingerprint="bb",
            )
        finally:
            reg.close()

        with mock.patch.object(
            uninstall_mod,
            "uninstall_gateway_over_ssh",
            return_value=["stopped"],
        ) as ssh_un:
            with mock.patch.object(uninstall_mod, "clear_local_secrets"):
                out = uninstall_mod.run_uninstall(
                    gateway=False,
                    monitoring=True,
                    sudo_password="secret",
                    stop_telemetry=lambda: None,
                    stop_services=lambda: None,
                    telemetry_uninstall=lambda: {
                        "ok": True,
                        "root": str(Path(self._td.name) / "telemetry"),
                        "errors": [],
                    },
                )
        self.assertEqual(ssh_un.call_count, 2)
        self.assertTrue(out.get("ok"))
        self.assertEqual(sorted(out["gateway"]["ids"]), ["gw-a", "gw-b"])
        # wipe_app_data removes registry; re-open should be empty / missing.
        if reg_path.is_file():
            reg2 = Registry(str(reg_path))
            try:
                self.assertEqual(reg2.list_gateways(), [])
            finally:
                reg2.close()

    def test_uninstall_gateway_only_keeps_telemetry(self) -> None:
        from pathlib import Path

        from shell import uninstall as uninstall_mod
        from shell.paths import save_settings

        tel = Path(self._td.name) / "telemetry" / "bin"
        tel.mkdir(parents=True)
        (tel / "loki").write_text("x", encoding="utf-8")
        save_settings(
            {
                "ssh_host": "192.168.1.50",
                "ssh_user": "ubuntu",
                "ssh_port": 22,
                "gateway_ip": "192.168.1.50",
                "default_gateway_id": "gateway",
                "monitoring_ip": "192.168.1.20",
            }
        )
        with mock.patch.object(
            uninstall_mod,
            "uninstall_gateway_over_ssh",
            return_value=["systemd stopped"],
        ) as ssh_un:
            with mock.patch.object(
                uninstall_mod, "remove_gateway_from_registry", return_value=True
            ):
                with mock.patch.object(uninstall_mod, "clear_local_secrets"):
                    out = uninstall_mod.run_uninstall(
                        gateway=True,
                        monitoring=False,
                        sudo_password="secret",
                        stop_telemetry=lambda: None,
                        stop_services=lambda: None,
                        telemetry_uninstall=lambda: (_ for _ in ()).throw(
                            AssertionError("must not wipe telemetry")
                        ),
                    )
        ssh_un.assert_called_once()
        self.assertTrue(out["ok"])
        self.assertTrue((tel / "loki").is_file())


if __name__ == "__main__":
    unittest.main()
