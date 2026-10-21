"""MQTT subscribe → decrypt → append sensor_data.log."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .decrypt import decrypt_wrapper_payload

logger = logging.getLogger(__name__)


class DecryptWorker:
    """Background MQTT client that writes cleartext sensor lines to a log file."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        topic: str = "sensors/#",
        username: str = "nodered",
        password: str = "",
        log_path: Path,
    ):
        self.host = host
        self.port = port
        self.topic = topic
        self.username = username
        self.password = password
        self.log_path = Path(log_path)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.messages_ok = 0
        self.messages_fail = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="decrypt-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed — decrypt worker idle")
            return

        def on_message(_client, _userdata, msg) -> None:
            clear = decrypt_wrapper_payload(msg.payload)
            if clear is None:
                self.messages_fail += 1
                return
            received_at = datetime.now(timezone.utc).isoformat()
            try:
                payload = json.loads(clear)
            except (TypeError, json.JSONDecodeError):
                payload = clear
            topic = str(msg.topic or "")
            topic_parts = topic.split("/")
            device_id = topic_parts[1] if len(topic_parts) > 1 else ""
            event = {
                "gateway_received_at": received_at,
                "device_reported_at": (
                    str(payload.get("timestamp") or "")
                    if isinstance(payload, dict)
                    else ""
                ),
                "event_type": "sensor_data",
                "deviceId": device_id,
                "username": device_id,
                "source_ip": "unknown",
                "topic": topic,
                "payload": payload,
                "payload_size_bytes": len(clear.encode("utf-8")),
            }
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            try:
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(line)
                self.messages_ok += 1
            except OSError as e:
                logger.warning("sensor_data.log write failed: %s", e)
                self.messages_fail += 1

        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id="iotgw-decrypt"
            )
        except AttributeError:
            client = mqtt.Client(client_id="iotgw-decrypt")
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        client.on_message = on_message

        while not self._stop.is_set():
            try:
                client.connect(self.host, self.port, keepalive=30)
                client.subscribe(self.topic)
                logger.info(
                    "decrypt worker subscribed %s:%s topic=%s → %s",
                    self.host,
                    self.port,
                    self.topic,
                    self.log_path,
                )
                while not self._stop.is_set():
                    client.loop(timeout=1.0)
                client.disconnect()
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("decrypt worker reconnecting: %s", e)
                time.sleep(2.0)
