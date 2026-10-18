"""Proxy Loki/Prometheus queries + Monitoring summary helpers."""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from registry.registry import Registry

from . import queries as Q
from .deps import AppState, get_registry, get_state, require_token

router = APIRouter(dependencies=[Depends(require_token)])

_TIMEOUT = 20.0


def _backend_urls(state: AppState) -> tuple[str, str]:
    return state.settings.loki_url.rstrip("/"), state.settings.prometheus_url.rstrip("/")


def _proxy_get(url: str, params: dict[str, Any]) -> Any:
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(url, params=params)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"backend unreachable: {e}",
        ) from e
    if r.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"backend {r.status_code}: {r.text[:400]}",
        )
    try:
        return r.json()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"backend returned non-JSON: {e}",
        ) from e


def _extract_scalar(data: Any) -> float:
    if not data:
        return 0.0
    try:
        result = data.get("data", {}).get("result", [])
        if not result:
            return 0.0
        r = result[0]
        if "value" in r:
            return float(r["value"][1])
        if "values" in r and r["values"]:
            return float(r["values"][-1][1])
    except (TypeError, ValueError, IndexError, KeyError):
        pass
    return 0.0


def _ns_window(range_s: str) -> tuple[int, int]:
    """Parse a simple range like 15m / 1h / 24h into (start_ns, end_ns)."""
    end = int(time.time())
    mult = 3600
    raw = range_s.strip().lower()
    try:
        if raw.endswith("m"):
            mult = int(raw[:-1]) * 60
        elif raw.endswith("h"):
            mult = int(raw[:-1]) * 3600
        elif raw.endswith("d"):
            mult = int(raw[:-1]) * 86400
        elif raw.endswith("s"):
            mult = int(raw[:-1])
        else:
            mult = int(raw)
    except ValueError:
        mult = 3600
    start = end - max(mult, 60)
    return start * 1_000_000_000, end * 1_000_000_000


def _monitored_ids(
    registry: Registry,
    gateway_id: Optional[str],
    *,
    show_all: bool,
) -> Optional[set[str]]:
    """Return allowed device ids, or None when filter should not apply.

    - ``show_all`` → None (raw Loki view).
    - Unknown ``gateway_id`` → 404.
    - No device rows for the selected gateway(s) → None (first-run explore).
    - Devices exist → set of ``monitor_enabled=1`` ids (may be empty).
    """
    if show_all:
        return None

    if gateway_id:
        gw = registry.get_gateway(gateway_id)
        if gw is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"gateway {gateway_id!r} not found",
            )
        gateways = [gw]
    else:
        gateways = registry.list_gateways()

    any_devices = False
    ids: set[str] = set()
    for gw in gateways:
        rows = registry.list_devices(gw["id"])
        if rows:
            any_devices = True
        for row in rows:
            if int(row.get("monitor_enabled", 1) or 0) == 1:
                ids.add(str(row["id"]))
    if not any_devices:
        return None
    return ids


@router.get("/query/loki")
def query_loki(
    query: str = Query(..., min_length=1),
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = Query(default=1000, ge=1, le=5000),
    direction: str = Query(default="backward"),
    time: Optional[str] = None,
    state: AppState = Depends(get_state),
) -> Any:
    """Proxy to Loki instant or range query."""
    loki, _ = _backend_urls(state)
    params: dict[str, Any] = {"query": query, "limit": limit, "direction": direction}
    if start is not None and end is not None:
        params["start"] = start
        params["end"] = end
        return _proxy_get(f"{loki}/loki/api/v1/query_range", params)
    if time is not None:
        params["time"] = time
    return _proxy_get(f"{loki}/loki/api/v1/query", params)


@router.get("/query/prometheus")
def query_prometheus(
    query: str = Query(..., min_length=1),
    start: Optional[str] = None,
    end: Optional[str] = None,
    step: Optional[str] = None,
    time: Optional[str] = None,
    state: AppState = Depends(get_state),
) -> Any:
    """Proxy to Prometheus instant or range query."""
    _, prom = _backend_urls(state)
    params: dict[str, Any] = {"query": query}
    if start is not None and end is not None:
        params["start"] = start
        params["end"] = end
        params["step"] = step or "60s"
        return _proxy_get(f"{prom}/api/v1/query_range", params)
    if time is not None:
        params["time"] = time
    return _proxy_get(f"{prom}/api/v1/query", params)


@router.get("/query/summaries/gateway")
def summary_gateway(
    range: str = Query(default="1h", alias="range"),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Gateway Activities summary (tables + scalars for native Monitoring tab)."""
    loki, prom = _backend_urls(state)
    start_ns, end_ns = _ns_window(range)
    duration = range
    warnings: list[str] = []

    def loki_scalar(expr: str) -> float:
        try:
            q = Q.prep_loki(expr, duration=duration)
            data = _proxy_get(
                f"{loki}/loki/api/v1/query_range",
                {
                    "query": q,
                    "start": start_ns,
                    "end": end_ns,
                    "limit": 100,
                    "direction": "backward",
                },
            )
            return _extract_scalar(data)
        except HTTPException as e:
            warnings.append(f"loki: {e.detail}")
            return 0.0

    def prom_scalar(expr: str) -> float:
        try:
            data = _proxy_get(f"{prom}/api/v1/query", {"query": expr})
            return _extract_scalar(data)
        except HTTPException as e:
            warnings.append(f"prometheus: {e.detail}")
            return 0.0

    total = int(loki_scalar(Q.GATEWAY_TOTAL_SENSOR_MSGS))
    msgs_min = loki_scalar(Q.GATEWAY_MSGS_PER_MIN)
    denied = int(
        loki_scalar(Q.GATEWAY_DENIED_NODERED)
        + loki_scalar(Q.GATEWAY_DENIED_HAPROXY)
        + loki_scalar(Q.GATEWAY_DENIED_MOSQUITTO)
    )
    ids_alerts = int(loki_scalar(Q.GATEWAY_IDS_ALERTS))
    allowed_ips = int(prom_scalar(Q.GATEWAY_ALLOWED_IP_COUNT))

    connected: list[dict[str, str]] = []
    try:
        data = _proxy_get(
            f"{prom}/api/v1/query", {"query": Q.GATEWAY_CONNECTED_SENSORS}
        )
        for r in data.get("data", {}).get("result", []) or []:
            m = r.get("metric") or {}
            dev = m.get("device_id") or m.get("deviceId")
            if not dev:
                continue
            connected.append(
                {
                    "device_id": str(dev),
                    "client_id": str(m.get("client_id") or ""),
                    "source_ip": str(m.get("source_ip") or ""),
                }
            )
    except HTTPException as e:
        warnings.append(f"connected_sensors: {e.detail}")

    delay_series: list[dict[str, Any]] = []
    try:
        q = Q.prep_loki(Q.GATEWAY_DELAY_BY_DEVICE, duration=duration)
        data = _proxy_get(
            f"{loki}/loki/api/v1/query_range",
            {
                "query": q,
                "start": start_ns,
                "end": end_ns,
                "limit": 500,
                "direction": "backward",
            },
        )
        for stream in data.get("data", {}).get("result", []) or []:
            metric = stream.get("metric") or {}
            spark = Q.sparkline_floats(stream.get("values") or [])
            delay_series.append(
                {
                    "device_id": metric.get("deviceId") or "?",
                    "latest": spark[-1] if spark else None,
                    "sparkline": spark,
                }
            )
    except HTTPException as e:
        warnings.append(f"delay: {e.detail}")

    return {
        "range": range,
        "metrics": {
            "total_sensor_messages": total,
            "messages_per_minute": round(msgs_min, 3),
            "denied_publishes": denied,
            "ids_detections": ids_alerts,
            "allowed_ips": allowed_ips,
        },
        "connected_sensors": connected,
        "delay_by_device": delay_series,
        "warnings": warnings,
    }


def _range_window_s(range_s: str) -> tuple[int, int, str]:
    """Return (start_unix, end_unix, step) for Prometheus range queries."""
    start_ns, end_ns = _ns_window(range_s)
    start = start_ns // 1_000_000_000
    end = end_ns // 1_000_000_000
    span = max(end - start, 60)
    if span <= 900:
        step = "15s"
    elif span <= 3600:
        step = "30s"
    elif span <= 21600:
        step = "60s"
    else:
        step = "120s"
    return start, end, step


def _container_label(metric: dict[str, Any]) -> str:
    raw = (
        metric.get("name")
        or metric.get("container_name")
        or metric.get("id")
        or "?"
    )
    name = str(raw).lstrip("/")
    if name.startswith("iot-"):
        name = name[4:]
    return name[:28] or "?"


@router.get("/query/summaries/raspi")
def summary_raspi(
    node: str = Query(default="gateway"),
    job: str = Query(default="node"),
    range: str = Query(default="1h", alias="range"),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Host (Raspi) metrics + chart series for native Monitoring Gateway Metric."""
    _, prom = _backend_urls(state)
    warnings: list[str] = []

    def prom_scalar(expr: str) -> float:
        try:
            q = Q.prep_prom(expr, node=node, job=job)
            data = _proxy_get(f"{prom}/api/v1/query", {"query": q})
            return _extract_scalar(data)
        except HTTPException as e:
            warnings.append(f"prometheus: {e.detail}")
            return 0.0

    def prom_max_scalar(expr: str) -> float:
        """Highest sample across a multi-series instant query (e.g. temps)."""
        try:
            q = Q.prep_prom(expr, node=node, job=job)
            data = _proxy_get(f"{prom}/api/v1/query", {"query": q})
            result = (data or {}).get("data", {}).get("result", []) or []
            best = 0.0
            found = False
            for r in result:
                try:
                    if "value" in r:
                        v = float(r["value"][1])
                    elif "values" in r and r["values"]:
                        v = float(r["values"][-1][1])
                    else:
                        continue
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                if not found or v > best:
                    best = v
                    found = True
            return best if found else 0.0
        except HTTPException as e:
            warnings.append(f"prometheus: {e.detail}")
            return 0.0

    def prom_spark(expr: str, *, scale: float = 1.0, label: str = "spark") -> list[float]:
        try:
            q = Q.prep_prom(expr, node=node, job=job)
            data = _proxy_get(
                f"{prom}/api/v1/query_range",
                {"query": q, "start": start, "end": end, "step": step},
            )
            for r in data.get("data", {}).get("result", []) or []:
                vals = Q.sparkline_floats(r.get("values") or [], limit=60)
                if scale != 1.0:
                    vals = [v * scale for v in vals]
                return vals
        except HTTPException as e:
            warnings.append(f"{label}: {e.detail}")
        return []

    def prom_named_bars(expr: str, *, limit: int = 8, label: str = "bars") -> list[dict[str, Any]]:
        try:
            q = Q.prep_prom(expr, node=node, job=job)
            data = _proxy_get(f"{prom}/api/v1/query", {"query": q})
            rows: list[dict[str, Any]] = []
            for r in (data.get("data", {}).get("result", []) or [])[:limit]:
                metric = r.get("metric") or {}
                rows.append(
                    {
                        "name": _container_label(metric),
                        "value": round(_extract_scalar({"data": {"result": [r]}}), 3),
                    }
                )
            return rows
        except HTTPException as e:
            warnings.append(f"{label}: {e.detail}")
            return []

    cpu = prom_scalar(Q.RASPI_CPU_USAGE)
    cores = prom_scalar(Q.RASPI_CPU_CORES)
    mem_total = prom_scalar(Q.RASPI_MEM_TOTAL)
    mem_used = prom_scalar(Q.RASPI_MEM_USED)
    load1 = prom_scalar(Q.RASPI_LOAD1)
    fs_avail = prom_scalar(Q.RASPI_FS_AVAIL)
    fs_size = prom_scalar(Q.RASPI_FS_SIZE)
    containers = int(prom_scalar(Q.RASPI_ACTIVE_CONTAINERS))
    up = prom_scalar(Q.RASPI_UP)
    temp_c = prom_max_scalar(Q.RASPI_TEMP_C)
    uptime_s = prom_scalar(Q.RASPI_UPTIME_S)
    swap_pct = prom_scalar(Q.RASPI_SWAP_PCT)
    iowait = prom_scalar(Q.RASPI_IOWAIT)
    procs_run = prom_scalar(Q.RASPI_PROCS_RUNNING)
    procs_blk = prom_scalar(Q.RASPI_PROCS_BLOCKED)

    start, end, step = _range_window_s(range)
    cpu_spark = prom_spark(Q.RASPI_CPU_BUSY_PCT_RANGE, label="cpu_sparkline")
    mem_spark = prom_spark(Q.RASPI_MEM_USED_PCT_RANGE, label="mem_sparkline")
    load_spark = prom_spark(Q.RASPI_LOAD1, label="load_sparkline")
    fs_spark = prom_spark(Q.RASPI_FS_USED_PCT_RANGE, label="fs_sparkline")
    net_rx_spark = prom_spark(Q.RASPI_NET_RX_MBPS, label="net_rx_sparkline")
    net_tx_spark = prom_spark(Q.RASPI_NET_TX_MBPS, label="net_tx_sparkline")
    temp_spark = prom_spark(Q.RASPI_TEMP_C, label="temp_sparkline")

    container_cpu = prom_named_bars(Q.RASPI_CONTAINER_CPU, label="container_cpu")
    container_mem = prom_named_bars(Q.RASPI_CONTAINER_MEM_MIB, label="container_mem")

    return {
        "node": node,
        "job": job,
        "range": range,
        "up": bool(up),
        "metrics": {
            "cpu_cores_busy": round(cpu, 3),
            "cpu_cores": int(cores) if cores else 0,
            "mem_used_bytes": int(mem_used),
            "mem_total_bytes": int(mem_total),
            "mem_used_pct": round((mem_used / mem_total) * 100, 1) if mem_total else 0.0,
            "load1": round(load1, 3),
            "fs_avail_bytes": int(fs_avail),
            "fs_size_bytes": int(fs_size),
            "fs_used_pct": (
                round((1 - fs_avail / fs_size) * 100, 1) if fs_size else 0.0
            ),
            "active_containers": containers,
            "temp_c": round(temp_c, 1) if temp_c else None,
            "uptime_s": int(uptime_s) if uptime_s else 0,
            "swap_pct": round(swap_pct, 1) if swap_pct else 0.0,
            "iowait_cores": round(iowait, 3) if iowait else 0.0,
            "procs_running": int(procs_run) if procs_run else 0,
            "procs_blocked": int(procs_blk) if procs_blk else 0,
        },
        "cpu_sparkline_pct": cpu_spark,
        "charts": {
            "cpu_pct": cpu_spark,
            "mem_used_pct": mem_spark,
            "load1": load_spark,
            "fs_used_pct": fs_spark,
            "net_rx_mbps": net_rx_spark,
            "net_tx_mbps": net_tx_spark,
            "temp_c": temp_spark,
        },
        "containers": {
            "cpu": container_cpu,
            "mem_mib": container_mem,
        },
        "warnings": warnings,
    }


@router.get("/query/summaries/sensors")
def summary_sensors(
    gateway_id: Optional[str] = None,
    show_all: bool = False,
    range: str = Query(default="1h", alias="range"),
    state: AppState = Depends(get_state),
    registry: Registry = Depends(get_registry),
) -> dict[str, Any]:
    """Sensors Reading summary; filters to monitor_enabled=1 when devices exist."""
    loki, _ = _backend_urls(state)
    start_ns, end_ns = _ns_window(range)
    allowed = _monitored_ids(registry, gateway_id, show_all=show_all)
    warnings: list[str] = []

    def loki_range(expr: str) -> Any:
        q = Q.prep_loki(expr, duration=range)
        return _proxy_get(
            f"{loki}/loki/api/v1/query_range",
            {
                "query": q,
                "start": start_ns,
                "end": end_ns,
                "limit": 1000,
                "direction": "backward",
            },
        )

    stages: list[dict[str, Any]] = []
    try:
        data = loki_range(Q.SENSORS_STAGE)
        result = Q.filter_series_by_device(
            data.get("data", {}).get("result", []) or [],
            allowed=allowed,
        )
        for stream in result:
            metric = stream.get("metric") or {}
            stage = metric.get("stage", "Unknown")
            values = stream.get("values") or []
            if values:
                stage = values[-1][1]
            stages.append(
                {
                    "device_id": metric.get("deviceId") or "?",
                    "stage": stage,
                }
            )
    except HTTPException as e:
        warnings.append(f"stages: {e.detail}")

    def series_sparks(expr: str, label: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            data = loki_range(expr)
            result = Q.filter_series_by_device(
                data.get("data", {}).get("result", []) or [],
                allowed=allowed,
            )
            for stream in result:
                metric = stream.get("metric") or {}
                spark = Q.sparkline_floats(stream.get("values") or [])
                out.append(
                    {
                        "device_id": metric.get("deviceId") or "?",
                        "series": label,
                        "latest": spark[-1] if spark else None,
                        "sparkline": spark,
                    }
                )
        except HTTPException as e:
            warnings.append(f"{label}: {e.detail}")
        return out

    temps = (
        series_sparks(Q.SENSORS_TEMP_ZONE1, "Zone 1")
        + series_sparks(Q.SENSORS_TEMP_ZONE2, "Zone 2")
        + series_sparks(Q.SENSORS_TEMP_ZONE3, "Zone 3")
        + series_sparks(Q.SENSORS_TEMP_CHAMBER, "Chamber")
    )
    pressures = series_sparks(Q.SENSORS_PRESSURE_CHAMBER, "Chamber")

    return {
        "range": range,
        "gateway_id": gateway_id,
        "show_all": show_all,
        "filter_active": allowed is not None,
        "monitored_device_ids": sorted(allowed) if allowed is not None else [],
        "stages": stages,
        "temperatures": temps,
        "pressures": pressures,
        "warnings": warnings,
    }
