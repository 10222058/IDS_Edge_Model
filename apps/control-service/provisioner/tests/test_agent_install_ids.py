"""The default provisioner must deploy the IDS package and model assets."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provisioner.agent_install import _upload_agent_tree
from provisioner.ssh import CommandResult


class _FakeSSH:
    def __init__(self) -> None:
        self.trees: list[tuple[str, str]] = []
        self.files: list[tuple[str, str]] = []

    def upload_tree(self, local_dir: Path, remote_dir: str) -> None:
        self.trees.append((str(local_dir), remote_dir))

    def upload_file(self, local_path: Path, remote_path: str, *, mode: int = 0o644) -> None:
        self.files.append((str(local_path), remote_path))


class AgentInstallIDSTests(unittest.TestCase):
    def test_uploads_detector_model_and_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = root / "gateway" / "agent"
            (agent / "iot_gateway_agent").mkdir(parents=True)
            (agent / "requirements.txt").write_text("lightgbm\n", encoding="utf-8")
            (agent / "iot-gateway-agent.service").write_text("[Unit]\n", encoding="utf-8")
            ids = root / "gateway" / "ids"
            for folder in ("ids_gateway", "models", "configs"):
                (ids / folder).mkdir(parents=True)
                (ids / folder / "asset").write_text("data", encoding="utf-8")

            ssh = _FakeSSH()
            with patch("provisioner.agent_install.sudo_run", return_value=CommandResult(0, "", "")):
                _upload_agent_tree(
                    ssh,
                    agent,
                    "/opt/iot-gateway-agent",
                    sudo_password="temporary",
                )

            self.assertIn(
                (str(ids / "ids_gateway"), "/opt/iot-gateway-agent/ids/ids_gateway"),
                ssh.trees,
            )
            self.assertIn(
                (str(ids / "models"), "/opt/iot-gateway-agent/ids/models"),
                ssh.trees,
            )
            self.assertIn(
                (str(ids / "configs"), "/opt/iot-gateway-agent/ids/configs"),
                ssh.trees,
            )


if __name__ == "__main__":
    unittest.main()
