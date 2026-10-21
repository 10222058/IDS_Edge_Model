"""Lifecycle wrapper for the gateway's rule + ML IDS process."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class IDSProcess:
    def __init__(self, ids_dir: Path, data_dir: Path) -> None:
        self.ids_dir = Path(ids_dir)
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / "ids_config.yaml"
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.is_file():
            source = self.ids_dir / "configs" / "ids_config.yaml"
            with source.open("r", encoding="utf-8") as fh:
                config = yaml.safe_load(fh)
            gateway = config.setdefault("gateway", {})
            gateway["sensor_log"]["path"] = str(
                self.data_dir / "logs" / "sensor_data.log"
            )
            gateway["alert_log_path"] = str(
                self.data_dir / "logs" / "ids-alerts.log"
            )
            gateway["mosquitto_log"]["path"] = str(
                self.data_dir / "mosquitto" / "log" / "mosquitto.log"
            )
            gateway["haproxy_log"]["path"] = ""
            with self.config_path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(config, fh, sort_keys=False)
        with self.config_path.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
        if not config.get("enabled", True):
            logger.info("IDS disabled by configuration")
            return
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.ids_dir) + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ids_gateway.main",
                str(self.config_path),
            ],
            cwd=self.ids_dir,
            env=env,
        )
        logger.info("IDS process started (pid=%s)", self.process.pid)

    def stop(self) -> None:
        proc = self.process
        self.process = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        logger.info("IDS process stopped")
