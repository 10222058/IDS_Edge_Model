"""Golden PromQL / LogQL expressions mirroring Streamlit dashboards.

Used by /api/v1/query/summaries/* and golden tests — keep in sync with
monitoring/build/streamlit/modules/dashboards/{gateway,raspi,sensors}.py.
"""

from __future__ import annotations

from typing import Optional

# ── Gateway Activities (gateway.py) ──────────────────────────────────────────
GATEWAY_TOTAL_SENSOR_MSGS = (
    'sum(count_over_time({container="iot-nodered", event_type="sensor_data"}[{range}]))'
)
GATEWAY_MSGS_PER_MIN = (
    'sum(rate({container="iot-nodered", event_type="sensor_data"}[{range}])) * 60'
)
GATEWAY_DENIED_NODERED = (
    'count_over_time({container="iot-nodered", event_type=~"denied_.*"}[{range}])'
)
GATEWAY_DENIED_HAPROXY = (
    'count_over_time({container="iot-haproxy"} |~ '
    '"mqtts_frontend|handshake|ssl|verify|certificate|c_verify=[1-9]|SC--|C--|reject|NOSRV" '
    "[{range}])"
)
GATEWAY_DENIED_MOSQUITTO = (
    'count_over_time({container="iot-mosquitto"} |~ "Denied PUBLISH" [{range}])'
)
GATEWAY_IDS_ALERTS = (
    'count_over_time({job="ids-alerts"} | json | event_type="ids_alert" [{range}])'
)
GATEWAY_ALLOWED_IP_COUNT = "allowed_ip_count"
GATEWAY_CONNECTED_SENSORS = "mqtt_connected_sensor"
GATEWAY_DELAY_BY_DEVICE = (
    'avg_over_time({container="iot-nodered", event_type="sensor_data"} '
    "| json | unwrap delay [5m]) by (deviceId)"
)

# ── Raspi / Host (raspi.py) ──────────────────────────────────────────────────
RASPI_CPU_USAGE = (
    'count(count(node_cpu_seconds_total{instance="$node",job="$job"}) by (cpu)) '
    '* (1 - avg(rate(node_cpu_seconds_total{instance="$node",job="$job",mode="idle"}[30s])))'
)
RASPI_CPU_CORES = (
    'count(count(node_cpu_seconds_total{instance="$node",job="$job"}) by (cpu))'
)
RASPI_MEM_TOTAL = 'node_memory_MemTotal_bytes{instance="$node",job="$job"}'
RASPI_MEM_USED = (
    'node_memory_MemTotal_bytes{instance="$node",job="$job"}'
    ' - node_memory_MemAvailable_bytes{instance="$node",job="$job"}'
)
RASPI_LOAD1 = 'node_load1{instance="$node",job="$job"}'
RASPI_FS_AVAIL = (
    'node_filesystem_avail_bytes{instance="$node",job="$job",mountpoint="/"}'
)
RASPI_FS_SIZE = (
    'node_filesystem_size_bytes{instance="$node",job="$job",mountpoint="/"}'
)
RASPI_ACTIVE_CONTAINERS = (
    'count(container_last_seen{instance="$node",job="$job",name=~".+"} > (time() - 120))'
)
RASPI_UP = 'up{instance="$node",job="$job"}'
RASPI_TEMP_C = 'node_hwmon_temp_celsius{instance="$node",job="$job"}'
RASPI_UPTIME_S = (
    'node_time_seconds{instance="$node",job="$job"}'
    ' - node_boot_time_seconds{instance="$node",job="$job"}'
)
RASPI_SWAP_PCT = (
    '((node_memory_SwapTotal_bytes{instance="$node",job="$job"}'
    ' - node_memory_SwapFree_bytes{instance="$node",job="$job"})'
    ' / node_memory_SwapTotal_bytes{instance="$node",job="$job"}) * 100'
)
RASPI_IOWAIT = (
    'sum by (instance)(irate(node_cpu_seconds_total'
    '{instance="$node",job="$job",mode="iowait"}[1m]))'
)
RASPI_PROCS_RUNNING = 'node_procs_running{instance="$node",job="$job"}'
RASPI_PROCS_BLOCKED = 'node_procs_blocked{instance="$node",job="$job"}'
RASPI_CPU_BUSY_PCT_RANGE = (
    '100 * (1 - avg(rate(node_cpu_seconds_total'
    '{instance="$node",job="$job",mode="idle"}[1m])))'
)
RASPI_MEM_USED_PCT_RANGE = (
    '100 * (1 - (node_memory_MemAvailable_bytes{instance="$node",job="$job"}'
    ' / node_memory_MemTotal_bytes{instance="$node",job="$job"}))'
)
RASPI_FS_USED_PCT_RANGE = (
    '100 * (1 - (node_filesystem_avail_bytes{instance="$node",job="$job",mountpoint="/"}'
    ' / node_filesystem_size_bytes{instance="$node",job="$job",mountpoint="/"}))'
)
RASPI_NET_RX_MBPS = (
    'sum(rate(node_network_receive_bytes_total'
    '{instance="$node",job="$job",device!="lo"}[1m])) * 8 / 1e6'
)
RASPI_NET_TX_MBPS = (
    'sum(rate(node_network_transmit_bytes_total'
    '{instance="$node",job="$job",device!="lo"}[1m])) * 8 / 1e6'
)
RASPI_CONTAINER_CPU = (
    'sort_desc(sum by (name) (rate(container_cpu_usage_seconds_total'
    '{instance="$node",job="$job",name=~".+"}[1m])))'
)
RASPI_CONTAINER_MEM_MIB = (
    'sort_desc(sum by (name) (container_memory_working_set_bytes'
    '{instance="$node",job="$job",name=~".+"}) / 1024 / 1024)'
)

# ── Sensors (sensors.py) ─────────────────────────────────────────────────────
SENSORS_STAGE = """last_over_time(
  {event_type="sensor_data"}
  | json
  | label_format stage="{{.payload_process_parameters_sinteringStage_value}}"
  [5m]
) by (deviceId)"""

SENSORS_TEMP_ZONE1 = (
    'max_over_time({event_type="sensor_data"} | json '
    "| unwrap payload_process_parameters_temperatureZone1_value [5m]) by (deviceId)"
)
SENSORS_TEMP_ZONE2 = (
    'max_over_time({event_type="sensor_data"} | json '
    "| unwrap payload_process_parameters_temperatureZone2_value [5m]) by (deviceId)"
)
SENSORS_TEMP_ZONE3 = (
    'max_over_time({event_type="sensor_data"} | json '
    "| unwrap payload_process_parameters_temperatureZone3_value [5m]) by (deviceId)"
)
SENSORS_TEMP_CHAMBER = (
    'max_over_time({event_type="sensor_data"} | json '
    "| unwrap payload_process_parameters_temperatureChamber_value [5m]) by (deviceId)"
)
SENSORS_PRESSURE_CHAMBER = (
    'max_over_time({event_type="sensor_data"} | json '
    "| unwrap payload_process_parameters_pressureChamber_value [5m]) by (deviceId)"
)


def prep_loki(expr: str, *, duration: str = "1h") -> str:
    return expr.replace("\r\n", "\n").replace("[{range}]", f"[{duration}]")


def prep_prom(expr: str, *, node: str = "gateway", job: str = "node") -> str:
    return expr.replace("\r\n", "\n").replace("$node", node).replace("$job", job)


def filter_series_by_device(
    result: list[dict],
    *,
    allowed: Optional[set[str]],
    label_keys: tuple[str, ...] = ("deviceId", "device_id"),
) -> list[dict]:
    """Drop series whose device label is not in ``allowed``.

    When ``allowed`` is None, return ``result`` unchanged.
    When filtering, series without a device label are dropped.
    """
    if allowed is None:
        return result
    out: list[dict] = []
    for series in result:
        metric = series.get("metric") or series.get("stream") or {}
        if not isinstance(metric, dict):
            continue
        did = None
        for k in label_keys:
            if metric.get(k):
                did = str(metric[k])
                break
        if did is not None and did in allowed:
            out.append(series)
    return out


def sparkline_floats(values: list, *, limit: int = 30) -> list[float]:
    """Convert Loki/Prom value pairs to floats; skip non-numeric points."""
    spark: list[float] = []
    for pair in values[-limit:]:
        try:
            spark.append(float(pair[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return spark
