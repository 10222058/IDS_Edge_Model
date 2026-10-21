"""IDS subprocess bootstrapping and gateway-specific paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from iot_gateway_agent.ids_runtime import IDSProcess


class IDSRuntimeTests(unittest.TestCase):
    def test_start_creates_runtime_config_and_launches_detector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_dir = root / "ids"
            (ids_dir / "configs").mkdir(parents=True)
            (ids_dir / "configs" / "ids_config.yaml").write_text(
                yaml.safe_dump({
                    "gateway": {
                        "sensor_log": {"path": "/legacy/sensor.log"},
                        "alert_log_path": "/legacy/alerts.log",
                        "mosquitto_log": {"path": "/legacy/mosquitto.log"},
                        "haproxy_log": {"path": "/legacy/haproxy.log"},
                    },
                    "model": {},
                }),
                encoding="utf-8",
            )
            data_dir = root / "data"
            process = IDSProcess(ids_dir, data_dir)
            child = Mock(pid=1234, poll=Mock(return_value=None))
            with patch("iot_gateway_agent.ids_runtime.subprocess.Popen", return_value=child) as popen:
                process.start()

            config = yaml.safe_load((data_dir / "ids_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["gateway"]["sensor_log"]["path"], str(data_dir / "logs" / "sensor_data.log"))
            self.assertEqual(config["gateway"]["alert_log_path"], str(data_dir / "logs" / "ids-alerts.log"))
            self.assertEqual(config["gateway"]["haproxy_log"]["path"], "")
            self.assertEqual(popen.call_args.args[0][1:3], ["-m", "ids_gateway.main"])
            self.assertEqual(popen.call_args.kwargs["cwd"], ids_dir)
            process.stop()
            child.terminate.assert_called_once()

    def test_disabled_runtime_does_not_launch_detector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_dir = root / "ids"
            (ids_dir / "configs").mkdir(parents=True)
            (ids_dir / "configs" / "ids_config.yaml").write_text(
                yaml.safe_dump({"enabled": False, "gateway": {
                    "sensor_log": {}, "alert_log_path": "",
                    "mosquitto_log": {}, "haproxy_log": {},
                }}),
                encoding="utf-8",
            )
            process = IDSProcess(ids_dir, root / "data")
            with patch("iot_gateway_agent.ids_runtime.subprocess.Popen") as popen:
                process.start()
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
