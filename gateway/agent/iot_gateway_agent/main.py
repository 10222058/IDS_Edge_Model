"""CLI entry for the monolithic IoT gateway agent (no Docker)."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from . import AGENT_PORT, __version__
from .broker import MosquittoSupervisor, write_mosquitto_conf
from .decrypt_worker import DecryptWorker
from .health import build_health, build_info
from .ids_runtime import IDSProcess
from .mdns import start_mdns, stop_mdns
from .metrics import AgentMetrics
from .server import create_server
from .shipper import LokiShipper

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="iot-gateway-agent",
        description=(
            "Monolithic IoT gateway agent: MQTT (mosquitto child), decrypt→log, "
            "Loki shipper, /metrics, mDNS — no Docker."
        ),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("AGENT_HOST", "0.0.0.0"),
        help="HTTP listen address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AGENT_PORT", AGENT_PORT)),
        help=f"HTTP listen port (default {AGENT_PORT})",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("IOTGW_AGENT_DATA", "/var/lib/iot-gateway-agent"),
        help="Config, mosquitto data, sensor_data.log",
    )
    parser.add_argument(
        "--monitoring-ip",
        default=os.environ.get("MONITORING_IP", "127.0.0.1"),
        help="Operator PC IP for Loki push",
    )
    parser.add_argument(
        "--loki-url",
        default=os.environ.get("LOKI_URL", ""),
        help="Override Loki base URL (default http://MONITORING_IP:3100)",
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=int(os.environ.get("MQTT_PORT", "1883")),
    )
    parser.add_argument(
        "--decrypt-user",
        default=os.environ.get("IOTGW_DECRYPT_USER", "nodered"),
    )
    parser.add_argument(
        "--decrypt-password",
        default=os.environ.get("IOTGW_DECRYPT_PASSWORD", ""),
    )
    parser.add_argument(
        "--no-broker",
        action="store_true",
        help="Do not spawn mosquitto (use an existing broker on localhost)",
    )
    parser.add_argument(
        "--no-decrypt",
        action="store_true",
        help="Disable decrypt worker",
    )
    parser.add_argument(
        "--no-shipper",
        action="store_true",
        help="Disable Loki shipper",
    )
    parser.add_argument("--no-mdns", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    conf_dir = data / "mosquitto"
    passwords = conf_dir / "passwords"
    acl = conf_dir / "acl"
    if not passwords.is_file():
        passwords.parent.mkdir(parents=True, exist_ok=True)
        passwords.write_text("", encoding="utf-8")
    if not acl.is_file():
        acl.write_text(
            "# agent-managed ACL\nuser nodered\ntopic readwrite sensors/#\n",
            encoding="utf-8",
        )

    conf = write_mosquitto_conf(
        conf_dir / "mosquitto.conf",
        data_dir=conf_dir / "data",
        log_dir=conf_dir / "log",
        passwords=passwords,
        acl=acl,
        listen_port=args.mqtt_port,
    )

    broker = MosquittoSupervisor(conf)
    worker: DecryptWorker | None = None
    shipper: LokiShipper | None = None
    ids_process = IDSProcess(Path(__file__).resolve().parents[1] / "ids", data)
    metrics = AgentMetrics()

    if not args.no_broker:
        try:
            broker.start()
            metrics.mqtt_up = broker.running()
        except Exception as e:  # noqa: BLE001
            logger.error("mosquitto start failed: %s", e)
            metrics.mqtt_up = False

    sensor_log = data / "logs" / "sensor_data.log"
    if not args.no_decrypt:
        worker = DecryptWorker(
            host="127.0.0.1",
            port=args.mqtt_port,
            username=args.decrypt_user,
            password=args.decrypt_password,
            log_path=sensor_log,
        )
        worker.start()
        metrics.decrypt_worker = worker

    ids_process.start()
    alert_log = data / "logs" / "ids-alerts.log"
    alert_shipper: LokiShipper | None = None
    loki = args.loki_url.strip() or f"http://{args.monitoring_ip}:3100"
    gateway_label = os.environ.get("IOTGW_GATEWAY_IP", "").strip() or None
    if not args.no_shipper:
        shipper = LokiShipper(sensor_log, loki_url=loki, gateway=gateway_label)
        shipper.start()
        alert_shipper = LokiShipper(
            alert_log,
            loki_url=loki,
            job="ids-alerts",
            gateway=gateway_label,
        )
        alert_shipper.start()
        metrics.shipper = shipper

    mdns_handle = None
    mdns_enabled = False
    if not args.no_mdns:
        mdns_handle, mdns_enabled = start_mdns(args.port)
    else:
        logger.info("mDNS disabled via --no-mdns")

    def health_fn():
        metrics.mqtt_up = broker.running() if not args.no_broker else metrics.mqtt_up
        h = build_health(mdns_enabled=mdns_enabled, port=args.port)
        h["mqtt_up"] = metrics.mqtt_up
        h["mode"] = "monolithic"
        return h

    def info_fn():
        info = build_info(port=args.port)
        info["api"] = [
            "GET /v1/health",
            "GET /v1/info",
            "GET /metrics",
            "POST /v1/reload",
        ]
        info["read_only"] = False
        info["mode"] = "monolithic"
        info["data_dir"] = str(data)
        return info

    def reload_fn():
        # Re-read ACL/passwords by restarting mosquitto child.
        if not args.no_broker:
            broker.stop()
            broker.start()
            metrics.mqtt_up = broker.running()
        return {"ok": True, "mqtt_up": metrics.mqtt_up}

    server = create_server(
        args.host,
        args.port,
        health_fn,
        info_fn,
        metrics_fn=metrics.render,
        reload_fn=reload_fn,
    )

    def _request_shutdown(signum: int, _frame: object) -> None:
        logger.info("signal %s; shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _request_shutdown)

    logger.info(
        "iot-gateway-agent %s monolithic on %s:%s data=%s loki=%s mdns=%s",
        __version__,
        args.host,
        args.port,
        data,
        loki,
        mdns_enabled,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt; shutting down")
    finally:
        server.server_close()
        if worker:
            worker.stop()
        if shipper:
            shipper.stop()
        if alert_shipper:
            alert_shipper.stop()
        ids_process.stop()
        broker.stop()
        stop_mdns(mdns_handle)
        logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
