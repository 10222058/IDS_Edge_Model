"""IDS configuration and alert history for provisioned gateways."""

from __future__ import annotations

import hashlib
import json
import shlex
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from provisioner.agent_install import DEFAULT_AGENT_DATA, sudo_run
from registry.registry import Registry

from .deps import (
    AppState,
    get_registry,
    get_state,
    open_ssh_for,
    require_gateway,
    require_token,
)
from .query import _backend_urls, _ns_window, _proxy_get

router = APIRouter(dependencies=[Depends(require_token)])
_ALERT_QUERY = '{job="ids-alerts"} | json | event_type="ids_alert"'
_IDS_CONFIG_PATH = f"{DEFAULT_AGENT_DATA}/ids_config.yaml"


class IDSConfigUpdate(BaseModel):
    enabled: bool
    rules_enabled: bool
    ml_enabled: bool
    threshold: float = Field(ge=0.0, le=1.0)
    max_message_rate_per_sec: float = Field(gt=0.0, le=1000.0)
    max_clock_skew_seconds: float = Field(ge=0.0, le=3600.0)
    max_connect_publish_ratio: float = Field(gt=0.0, le=1000.0)
    alert_cooldown_seconds: int = Field(ge=0, le=86400)
    sudo_password: str = Field(min_length=1, max_length=256)


def _read_config(ssh: Any) -> dict[str, Any]:
    try:
        text = ssh.read_text(_IDS_CONFIG_PATH)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="IDS is not installed on this gateway yet. Provision the gateway again.",
        ) from exc
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"gateway IDS configuration is invalid: {exc}",
        ) from exc
    if not isinstance(config, dict) or not isinstance(config.get("gateway"), dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="gateway IDS configuration has an unexpected shape",
        )
    return config


def _config_values(config: dict[str, Any]) -> dict[str, Any]:
    gateway = config["gateway"]
    rules = gateway.get("behavioral_rules") or {}
    return {
        "enabled": bool(config.get("enabled", True)),
        "rules_enabled": bool(rules.get("enabled", True)),
        "ml_enabled": bool(gateway.get("ml_enabled", True)),
        "threshold": float(
            0.05
            if gateway.get("suspicious_threshold") is None
            else gateway["suspicious_threshold"]
        ),
        "max_message_rate_per_sec": float(
            rules.get("max_message_rate_per_sec", 1.5)
        ),
        "max_clock_skew_seconds": float(rules.get("max_clock_skew_seconds", 2.5)),
        "max_connect_publish_ratio": float(
            rules.get("max_connect_publish_ratio", 1.15)
        ),
        "alert_cooldown_seconds": int(rules.get("alert_cooldown_seconds", 60)),
    }


@router.get("/gateways/{gateway_id}/ids/config")
def get_ids_config(
    gateway_id: str,
    state: AppState = Depends(get_state),
    registry: Registry = Depends(get_registry),
) -> dict[str, Any]:
    gateway = require_gateway(registry, gateway_id)
    try:
        with open_ssh_for(state, gateway) as ssh:
            return _config_values(_read_config(ssh))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH/IDS config read failed: {exc}",
        ) from exc


@router.post("/gateways/{gateway_id}/ids/config")
def update_ids_config(
    gateway_id: str,
    body: IDSConfigUpdate,
    state: AppState = Depends(get_state),
    registry: Registry = Depends(get_registry),
) -> dict[str, Any]:
    gateway = require_gateway(registry, gateway_id)
    try:
        with open_ssh_for(state, gateway) as ssh:
            config = _read_config(ssh)
            rules = config["gateway"].setdefault("behavioral_rules", {})
            config["enabled"] = body.enabled
            rules["enabled"] = body.rules_enabled
            config["gateway"]["ml_enabled"] = body.ml_enabled
            config["gateway"]["suspicious_threshold"] = body.threshold
            config["gateway"]["ip_avg_score_threshold"] = body.threshold
            rules["max_message_rate_per_sec"] = body.max_message_rate_per_sec
            rules["max_clock_skew_seconds"] = body.max_clock_skew_seconds
            rules["max_connect_publish_ratio"] = body.max_connect_publish_ratio
            rules["alert_cooldown_seconds"] = body.alert_cooldown_seconds
            serialized = yaml.safe_dump(config, sort_keys=False)

            with tempfile.NamedTemporaryFile(
                "w", suffix=".yaml", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(serialized)
                local_path = Path(tmp.name)
            try:
                remote_tmp = f"/tmp/iotgw-ids-config-{uuid.uuid4().hex}.yaml"
                ssh.upload_file(local_path, remote_tmp, mode=0o600)
                command = (
                    f"install -o root -g root -m 0644 {shlex.quote(remote_tmp)} "
                    f"{shlex.quote(_IDS_CONFIG_PATH)} && "
                    "systemctl restart iot-gateway-agent; "
                    "result=$?; "
                    f"rm -f {shlex.quote(remote_tmp)}; "
                    "exit \"$result\""
                )
                result = sudo_run(
                    ssh,
                    f"sh -c {shlex.quote(command)}",
                    sudo_password=body.sudo_password,
                    timeout=60.0,
                )
                if result.exit_code != 0:
                    raise RuntimeError(
                        (result.stderr or result.stdout or "systemd restart failed").strip()
                    )
            finally:
                local_path.unlink(missing_ok=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH/IDS config update failed: {exc}",
        ) from exc

    registry.audit(
        "ids_config_updated",
        gateway_id=gateway_id,
        detail={
            key: value
            for key, value in _config_values(config).items()
            if key != "sudo_password"
        },
    )
    return {"ok": True, "config": _config_values(config)}


@router.get("/ids/alerts")
def get_ids_alerts(
    range: str = Query(default="1h", pattern=r"^(15m|1h|6h|24h|7d)$"),
    severity: str | None = Query(default=None, pattern=r"^(critical|warning|info)$"),
    limit: int = Query(default=500, ge=1, le=2000),
    gateway_id: str | None = Query(default=None, max_length=64),
    state: AppState = Depends(get_state),
    registry: Registry = Depends(get_registry),
) -> dict[str, Any]:
    loki, _ = _backend_urls(state)
    start_ns, end_ns = _ns_window(range)
    query = _ALERT_QUERY
    if gateway_id:
        gateway = registry.get_gateway(gateway_id)
        if gateway is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"gateway {gateway_id!r} not found",
            )
        query = (
            '{job="ids-alerts", gateway='
            + json.dumps(str(gateway["host"]))
            + '} | json | event_type="ids_alert"'
        )

    result = _proxy_get(
        f"{loki}/loki/api/v1/query_range",
        {
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": limit,
            "direction": "backward",
        },
    )
    alerts: list[dict[str, Any]] = []
    for stream in result.get("data", {}).get("result", []) or []:
        for timestamp, line in stream.get("values", []) or []:
            try:
                alert = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(alert, dict):
                continue
            if severity and str(alert.get("severity", "")).lower() != severity:
                continue
            alert["id"] = hashlib.sha256(
                f"{timestamp}:{line}".encode("utf-8")
            ).hexdigest()[:20]
            alert.setdefault("occurred_at", alert.get("@timestamp") or timestamp)
            alerts.append(alert)
    alerts.sort(key=lambda item: str(item.get("occurred_at", "")), reverse=True)
    return {"range": range, "count": len(alerts), "alerts": alerts}
