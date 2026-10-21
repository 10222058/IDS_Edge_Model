"""IDS config and alert routes."""

from __future__ import annotations

import json
import tempfile
import unittest
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from api.app import create_app
from api.settings import Settings
from registry.registry import Registry


class _FakeSSH:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = yaml.safe_dump(config)
        self.uploaded: bytes | None = None

    def read_text(self, path: str) -> str:
        self.assert_config_path(path)
        return self.config

    def upload_file(self, local_path: Path, remote_path: str, *, mode: int = 0o644) -> None:
        if not re.fullmatch(r"/tmp/iotgw-ids-config-[0-9a-f]{32}\.yaml", remote_path):
            raise AssertionError(f"unexpected temporary path: {remote_path}")
        self.uploaded = Path(local_path).read_bytes()

    @staticmethod
    def assert_config_path(path: str) -> None:
        if path != "/var/lib/iot-gateway-agent/ids_config.yaml":
            raise AssertionError(f"unexpected IDS config path: {path}")


class IDSApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.registry = Registry(str(root / "registry.sqlite"))
        self.addCleanup(self.registry.close)
        self.registry.upsert_gateway(
            id="gw1",
            host="192.0.2.8",
            ssh_user="iot",
            install_root="/opt/iot-gateway",
            fingerprint="pinned",
        )
        self.config = {
            "enabled": True,
            "gateway": {
                "suspicious_threshold": 0.05,
                "ml_enabled": True,
                "behavioral_rules": {
                    "enabled": True,
                    "max_message_rate_per_sec": 1.5,
                    "max_clock_skew_seconds": 2.5,
                    "max_connect_publish_ratio": 1.15,
                    "alert_cooldown_seconds": 60,
                },
            },
        }
        self.ssh = _FakeSSH(self.config)

        @contextmanager
        def open_ssh(_gateway: dict[str, Any]) -> Iterator[_FakeSSH]:
            yield self.ssh

        self.open_ssh = open_ssh
        settings = Settings(
            data_dir=root,
            registry_path=root / "registry.sqlite",
            loki_url="http://127.0.0.1:3100",
        )
        app = create_app(settings=settings, registry=self.registry, open_ssh=open_ssh)
        self.client = TestClient(app)

    def test_read_ids_config_over_ssh(self) -> None:
        response = self.client.get("/api/v1/gateways/gw1/ids/config")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["threshold"], 0.05)
        self.assertTrue(response.json()["ml_enabled"])

    def test_update_ids_config_validates_and_restarts_agent(self) -> None:
        with patch("api.ids.sudo_run") as run_sudo:
            run_sudo.return_value.exit_code = 0
            response = self.client.post(
                "/api/v1/gateways/gw1/ids/config",
                json={
                    "enabled": True,
                    "rules_enabled": False,
                    "ml_enabled": True,
                    "threshold": 0.42,
                    "max_message_rate_per_sec": 2.5,
                    "max_clock_skew_seconds": 3.0,
                    "max_connect_publish_ratio": 1.7,
                    "alert_cooldown_seconds": 30,
                    "sudo_password": "temporary-sudo-password",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertIsNotNone(self.ssh.uploaded)
        saved = yaml.safe_load(self.ssh.uploaded)
        self.assertFalse(saved["gateway"]["behavioral_rules"]["enabled"])
        self.assertEqual(saved["gateway"]["suspicious_threshold"], 0.42)
        self.assertEqual(saved["gateway"]["ip_avg_score_threshold"], 0.42)
        self.assertTrue(saved["gateway"]["ml_enabled"])
        self.assertEqual(run_sudo.call_args.kwargs["sudo_password"], "temporary-sudo-password")
        self.assertNotIn("sudo_password", json.dumps(response.json()))

    def test_alerts_parse_payload_and_filter_gateway(self) -> None:
        line = json.dumps({
            "event_type": "ids_alert",
            "severity": "critical",
            "device_id": "sensor4",
            "detection": "message_pattern_anomaly",
            "score": 0.91,
            "@timestamp": "2026-10-21T12:30:00+00:00",
        })
        with patch(
            "api.ids._proxy_get",
            return_value={"data": {"result": [{"values": [["1", line]]}]}},
        ) as query:
            response = self.client.get(
                "/api/v1/ids/alerts?range=1h&gateway_id=gw1"
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["alerts"][0]["device_id"], "sensor4")
        self.assertTrue(result["alerts"][0]["id"])
        self.assertIn('gateway="192.0.2.8"', query.call_args.args[1]["query"])

    def test_missing_gateway_is_404(self) -> None:
        self.assertEqual(
            self.client.get("/api/v1/gateways/missing/ids/config").status_code,
            404,
        )
