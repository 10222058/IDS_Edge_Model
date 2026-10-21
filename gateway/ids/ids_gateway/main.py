from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .alerts import build_alert_payload
from .config import load_config
from .detector import Detector
from .gateway_state import GatewayStateReader
from .log_capture import HaproxyLogCapture, MosquittoLogCapture, SensorLogCapture
from .model import IDSModel
from .rules import DeviceLogContext, RuleEngine
from .utils import device_id_from_topic

INTERNAL_TOPIC_PREFIXES = ("$SYS/",)
SENSOR_TOPIC_PREFIX = "sensors/"


def _is_internal_topic(topic: str) -> bool:
    return any(topic.startswith(prefix) for prefix in INTERNAL_TOPIC_PREFIXES)


def _is_sensor_topic(topic: str) -> bool:
    return topic.startswith(SENSOR_TOPIC_PREFIX)


def _describe_batch(records):
    first_record = records[0]
    topic = first_record.mqtt_topic or "unknown"
    device_id = (
        first_record.mqtt_device_id
        or device_id_from_topic(topic)
        or first_record.src_ip
    )
    observed_src = first_record.observed_src_ip or first_record.src_ip
    return device_id, topic, observed_src


def _split_into_model_windows(records, packet_window_seconds: int):
    if not records:
        return []
    packet_window_seconds = max(int(packet_window_seconds), 1)
    ordered = sorted(records, key=lambda r: (float(r.ts), r.mqtt_topic))
    buckets: dict[int, list] = defaultdict(list)
    for rec in ordered:
        bucket = int(float(rec.ts) // packet_window_seconds)
        buckets[bucket].append(rec)
    return [buckets[k] for k in sorted(buckets)]


class AlertWriter:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, payload: dict) -> None:
        payload.setdefault("event_type", "ids_alert")
        payload.setdefault("@timestamp", datetime.now(timezone.utc).isoformat())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run(config_path: str = "configs/ids_config.yaml") -> None:
    cfg = load_config(config_path)
    gateway_cfg = cfg["gateway"]
    model_cfg = cfg["model"]

    sensor_log_cfg = gateway_cfg.get("sensor_log", {})
    capture = SensorLogCapture(
        file_path=sensor_log_cfg.get("path", "/app/nodered_logs/sensor_data.log"),
        use_mac=bool(gateway_cfg.get("mac_tracking", False)),
    )

    mosquitto_log_cfg = gateway_cfg.get("mosquitto_log", {})
    mosquitto_capture = None
    mosquitto_log_path = mosquitto_log_cfg.get("path")
    if mosquitto_log_path:
        mosquitto_capture = MosquittoLogCapture(
            file_path=mosquitto_log_path,
            window_seconds=int(mosquitto_log_cfg.get("connect_window_seconds", 60)),
        )

    haproxy_log_cfg = gateway_cfg.get("haproxy_log", {})
    haproxy_capture = None
    haproxy_log_path = haproxy_log_cfg.get("path")
    if haproxy_log_path:
        haproxy_capture = HaproxyLogCapture(
            file_path=haproxy_log_path,
            window_seconds=int(haproxy_log_cfg.get("window_seconds", 60)),
        )

    model = IDSModel(
        model_path=model_cfg["path"],
        scaler_path=model_cfg.get("scaler_path"),
        features=model_cfg["expected_features"],
    )

    configured_threshold = gateway_cfg.get("suspicious_threshold")
    if configured_threshold is None:
        configured_threshold = model.get_default_threshold()
        threshold_source = "model_metadata"
    else:
        threshold_source = "config"
    if configured_threshold is None:
        configured_threshold = 0.05
        threshold_source = "default"

    behavioral_rules = gateway_cfg.get("behavioral_rules", {})
    rule_engine = RuleEngine(behavioral_config=behavioral_rules)

    gateway_state_cfg = gateway_cfg.get("gateway_state", {})
    gateway_state_reader = GatewayStateReader(
        active_sensors_prom=gateway_state_cfg.get(
            "active_sensors_prom", "/textfile/active_sensors.prom"
        ),
        allowed_ips_prom=gateway_state_cfg.get(
            "allowed_ips_prom", "/textfile/allowed_ips.prom"
        ),
        gateway_state_log=gateway_state_cfg.get(
            "gateway_state_log", "/var/log/iot-gateway/gateway-state.log"
        ),
        refresh_interval_seconds=float(
            gateway_state_cfg.get("refresh_interval_seconds", 1)
        ),
    )
    initial_state = gateway_state_reader.refresh_if_needed(force=True)
    rule_engine.update_gateway_state(initial_state)

    min_model_packets = int(
        gateway_cfg.get("min_model_packets", gateway_cfg.get("min_pkts", 1))
    )
    detector = Detector(
        model=model,
        threshold=float(configured_threshold),
        rule_engine=rule_engine,
        min_packets_per_window=min_model_packets,
        ml_enabled=bool(gateway_cfg.get("ml_enabled", True)),
    )

    packet_window_seconds = int(gateway_cfg.get("packet_window_seconds", 3))
    ip_aggregation_window = int(gateway_cfg.get("ip_aggregation_window", 6))
    min_windows_for_decision = int(gateway_cfg.get("min_windows_for_decision", 4))
    min_attack_windows = int(gateway_cfg.get("min_attack_windows", 3))
    attack_vote_ratio_threshold = float(gateway_cfg.get("attack_vote_ratio_threshold", 0.6))
    ip_avg_score_threshold = float(
        gateway_cfg.get("ip_avg_score_threshold", float(configured_threshold))
    )

    alert_writer = AlertWriter(
        gateway_cfg.get("alert_log_path", "/var/log/iot-gateway/ids-alerts.log")
    )

    device_window_state: dict[str, list[tuple[float, bool]]] = defaultdict(list)

    loop_interval_seconds = float(
        gateway_cfg.get(
            "loop_interval_seconds",
            gateway_cfg.get("inference_interval_seconds", 1),
        )
    )
    loop_interval_seconds = max(loop_interval_seconds, 0.1)

    print(f"[IDS] sensor log source: {capture.file_path}")
    if mosquitto_capture is not None:
        print(f"[IDS] mosquitto log source: {mosquitto_capture.file_path}")
    if haproxy_capture is not None:
        print(f"[IDS] haproxy log source: {haproxy_capture.file_path}")
    print(f"[IDS] alert log: {alert_writer.path}")
    print(f"[IDS] threshold: {float(configured_threshold):.6f} ({threshold_source})")
    print(f"[IDS] behavioral rules enabled: {behavioral_rules.get('enabled', True)}")
    print(
        "[IDS] gateway state: "
        f"source={initial_state.source} "
        f"allowed_ips={sorted(initial_state.allowed_ips)} "
        f"acl_users={sorted(initial_state.acl_users)} "
        f"device_ips={initial_state.device_source_ips}"
    )
    print(
        "[IDS] decision smoothing: "
        f"window={ip_aggregation_window} min_windows={min_windows_for_decision} "
        f"min_attack_windows={min_attack_windows} vote_ratio={attack_vote_ratio_threshold:.2f} "
        f"avg_score_threshold={ip_avg_score_threshold:.4f}"
    )
    print(
        f"[IDS] Gateway IDS loop started "
        f"(interval={loop_interval_seconds:.2f}s, post-gateway behavioral + ML)"
    )

    last_state_signature = ""

    while True:
        loop_started = time.time()
        gateway_state = gateway_state_reader.refresh_if_needed()
        rule_engine.update_gateway_state(gateway_state)
        state_signature = (
            f"{gateway_state.source}|"
            f"{sorted(gateway_state.allowed_ips)}|"
            f"{sorted(gateway_state.acl_users)}|"
            f"{sorted(gateway_state.device_source_ips.items())}"
        )
        if state_signature != last_state_signature:
            print(
                "[IDS] gateway state refreshed: "
                f"source={gateway_state.source} "
                f"allowed_ips={sorted(gateway_state.allowed_ips)} "
                f"acl_users={sorted(gateway_state.acl_users)} "
                f"device_ips={gateway_state.device_source_ips}"
            )
            last_state_signature = state_signature

        packets_by_device = capture.poll_once()
        denied_counts = capture.consume_denied_counts()
        rule_engine.ingest_denied_counts(denied_counts)

        mqtt_connect_stats = {}
        if mosquitto_capture is not None:
            mqtt_connect_stats = mosquitto_capture.poll_once()

        ssl_stats_by_ip = {}
        if haproxy_capture is not None:
            ssl_stats_by_ip = haproxy_capture.poll_once()

        device_ids = set(packets_by_device.keys()) | set(denied_counts.keys())
        for device_key in device_ids:
            records = packets_by_device.get(device_key, [])
            if records:
                windows = _split_into_model_windows(records, packet_window_seconds)
            else:
                windows = []

            if not windows:
                denied_stats = denied_counts.get(device_key)
                if denied_stats is None:
                    continue
                device_id = device_key
                observed_src = ""
                device_context = DeviceLogContext(
                    denied=denied_stats,
                    mqtt_connect=mqtt_connect_stats.get(device_id),
                    ssl_stats=None,
                )
                rule_passed, violation = rule_engine.evaluate(
                    device_id=device_id,
                    src_ip=observed_src,
                    records=[],
                    context=device_context,
                )
                if not rule_passed and violation is not None:
                    alert = build_alert_payload(
                        device_id=device_id,
                        source_ip=observed_src,
                        topic="",
                        alert_type="behavioral_rule",
                        detection=violation.violation_type,
                        severity=violation.severity,
                        message=violation.message,
                        score=1.0,
                        score_type="rule_confidence",
                    )
                    alert_writer.write(alert)
                    print(
                        f"[IDS] BEHAVIORAL device={device_id} src={observed_src} "
                        f"type={violation.violation_type} severity={violation.severity} "
                        f"msg={violation.message}"
                    )
                continue

            for window_records in windows:
                device_id, topic, observed_src = _describe_batch(window_records)

                if _is_internal_topic(topic):
                    continue
                if not _is_sensor_topic(topic):
                    continue

                device_context = DeviceLogContext(
                    denied=denied_counts.get(device_id),
                    mqtt_connect=mqtt_connect_stats.get(device_id),
                    ssl_stats=ssl_stats_by_ip.get(observed_src),
                )
                results = detector.evaluate_batch(
                    {device_id: window_records},
                    source_ips={device_id: observed_src},
                    contexts={device_id: device_context},
                )

                for result in results:
                    if not result.rule_passed:
                        violation = result.rule_violation
                        alert = build_alert_payload(
                            device_id=result.device_id,
                            source_ip=result.src_ip,
                            topic=topic,
                            alert_type="behavioral_rule",
                            detection=violation.violation_type,
                            severity=violation.severity,
                            message=violation.message,
                            score=1.0,
                            score_type="rule_confidence",
                        )
                        alert_writer.write(alert)
                        print(
                            f"[IDS] BEHAVIORAL device={device_id} src={result.src_ip} "
                            f"type={violation.violation_type} severity={violation.severity} "
                            f"msg={violation.message}"
                        )
                        continue

                    if result.skipped_reason:
                        print(
                            f"[IDS] device={device_id} src={result.src_ip} "
                            f"skip={result.skipped_reason} "
                            f"score={result.attack_probability:.6f} "
                            f"pkt_count={int(result.features.get('msg_count', 0.0))}"
                        )
                        continue

                    print(
                        f"[IDS] device={device_id} src={result.src_ip} "
                        f"score={result.attack_probability:.6f} attack={result.is_attack} "
                        f"pkt_count={result.features.get('msg_count', 0.0):.0f} "
                        f"iat_ms={result.features.get('mean_inter_arrival_ms', 0.0):.2f}"
                    )

                    state = device_window_state[result.device_id]
                    state.append((result.attack_probability, result.is_attack))
                    if len(state) > ip_aggregation_window:
                        state.pop(0)

                    attack_windows = sum(1 for _, is_attack in state if is_attack)
                    windows_seen = len(state)
                    vote_ratio = (attack_windows / windows_seen) if windows_seen else 0.0
                    avg_score = sum(score for score, _ in state) / windows_seen if windows_seen else 0.0

                    confirmed_attack = False
                    if windows_seen >= min_windows_for_decision:
                        confirmed_attack = (
                            (
                                attack_windows >= min_attack_windows
                                and vote_ratio >= attack_vote_ratio_threshold
                            )
                            or avg_score >= ip_avg_score_threshold
                        )

                    print(
                        f"[IDS] device_eval id={result.device_id} windows={windows_seen} "
                        f"attack_windows={attack_windows} vote_ratio={vote_ratio:.2f} "
                        f"avg_score={avg_score:.6f} confirmed={confirmed_attack}"
                    )

                    if confirmed_attack:
                        alert = build_alert_payload(
                            device_id=result.device_id,
                            source_ip=result.src_ip,
                            topic=topic,
                            alert_type="ml_anomaly",
                            detection="message_pattern_anomaly",
                            severity="critical",
                            message=(
                                f"ML model flagged anomalous message patterns for {device_id}"
                            ),
                            score=result.attack_probability,
                            score_type="ml_probability",
                            extra={
                                "avg_score": round(avg_score, 6),
                                "vote_ratio": round(vote_ratio, 4),
                            },
                        )
                        alert_writer.write(alert)
                        print(
                            f"[IDS] ALERT device={device_id} confirmed_attack "
                            f"score={result.attack_probability:.6f}"
                        )

        elapsed = time.time() - loop_started
        remaining = loop_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the IDS gateway")
    parser.add_argument(
        "config_path",
        nargs="?",
        default="configs/ids_config.yaml",
        help="Path to the YAML config file",
    )
    args = parser.parse_args()
    run(args.config_path)