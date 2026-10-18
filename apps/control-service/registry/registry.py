"""SQLite-backed registry for gateways, devices, allow-list IPs, and audit."""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable, Optional, TypeVar

from .schema import SCHEMA_SQL

# Distinguishes "caller omitted this kwarg" from explicit None (clear field).
_UNSET: Any = object()

_F = TypeVar("_F", bound=Callable[..., Any])


def _locked(method: _F) -> _F:
    """Serialize SQLite use across FastAPI's sync worker threads."""

    @functools.wraps(method)
    def wrapped(self: "Registry", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _now() -> int:
    return int(time.time())


def _json_text(value: Any) -> Optional[str]:
    """Serialize list/dict values to JSON text; pass through str/None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class Registry:
    """Local device/gateway registry backed by registry.sqlite."""

    def __init__(self, path: str):
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        # FastAPI runs sync routes in a worker thread pool; allow shared conn.
        self._conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    @_locked
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── gateways ──────────────────────────────────────────────────────

    @_locked
    def upsert_gateway(
        self,
        id: str,
        host: str,
        ssh_user: str,
        install_root: str,
        fingerprint: str,
        *,
        agent_url: Any = _UNSET,
        monitoring_ip: Any = _UNSET,
        password_auth_enabled: Any = _UNSET,
        created_at: Optional[int] = None,
        last_seen_at: Any = _UNSET,
        status: Any = _UNSET,
    ) -> dict[str, Any]:
        """Insert or update a gateway.

        Required identity fields always write. Optional kwargs use unset-sentinel
        semantics: omitted fields keep prior values (or insert defaults);
        explicit None clears a nullable column. ``last_seen_at`` is only
        rewritten when the caller passes it (insert defaults to now).
        """
        existing = self.get_gateway(id)
        ts = _now()
        if existing is None:
            row = {
                "id": id,
                "host": host,
                "ssh_user": ssh_user,
                "install_root": install_root,
                "fingerprint": fingerprint,
                "agent_url": None if agent_url is _UNSET else agent_url,
                "monitoring_ip": None if monitoring_ip is _UNSET else monitoring_ip,
                "password_auth_enabled": (
                    0
                    if password_auth_enabled is _UNSET
                    else int(password_auth_enabled)
                ),
                "created_at": created_at if created_at is not None else ts,
                "last_seen_at": ts if last_seen_at is _UNSET else last_seen_at,
                "status": None if status is _UNSET else status,
            }
        else:
            row = {
                "id": id,
                "host": host,
                "ssh_user": ssh_user,
                "install_root": install_root,
                "fingerprint": fingerprint,
                "agent_url": (
                    existing["agent_url"] if agent_url is _UNSET else agent_url
                ),
                "monitoring_ip": (
                    existing["monitoring_ip"]
                    if monitoring_ip is _UNSET
                    else monitoring_ip
                ),
                "password_auth_enabled": (
                    existing["password_auth_enabled"]
                    if password_auth_enabled is _UNSET
                    else int(password_auth_enabled)
                ),
                "created_at": existing["created_at"],
                "last_seen_at": (
                    existing["last_seen_at"]
                    if last_seen_at is _UNSET
                    else last_seen_at
                ),
                "status": existing["status"] if status is _UNSET else status,
            }
        self._conn.execute(
            """
            INSERT INTO gateways (
              id, host, ssh_user, install_root, agent_url, fingerprint,
              monitoring_ip, password_auth_enabled, created_at, last_seen_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              host = excluded.host,
              ssh_user = excluded.ssh_user,
              install_root = excluded.install_root,
              agent_url = excluded.agent_url,
              fingerprint = excluded.fingerprint,
              monitoring_ip = excluded.monitoring_ip,
              password_auth_enabled = excluded.password_auth_enabled,
              last_seen_at = excluded.last_seen_at,
              status = excluded.status
            """,
            (
                row["id"],
                row["host"],
                row["ssh_user"],
                row["install_root"],
                row["agent_url"],
                row["fingerprint"],
                row["monitoring_ip"],
                row["password_auth_enabled"],
                row["created_at"],
                row["last_seen_at"],
                row["status"],
            ),
        )
        self._conn.commit()
        out = self.get_gateway(id)
        assert out is not None
        return out

    @_locked
    def get_gateway(self, gateway_id: str) -> Optional[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM gateways WHERE id = ?", (gateway_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @_locked
    def list_gateways(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM gateways ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    # ── devices ───────────────────────────────────────────────────────

    @_locked
    def upsert_device(
        self,
        gateway_id: str,
        id: str,
        *,
        ip: Any = _UNSET,
        topics_rw: Any = _UNSET,
        topics_r: Any = _UNSET,
        monitor_enabled: Any = _UNSET,
        cert_expires_at: Any = _UNSET,
        cert_fingerprint: Any = _UNSET,
        created_at: Optional[int] = None,
        meta_json: Any = _UNSET,
    ) -> dict[str, Any]:
        """Insert or update a device.

        Omitted optional kwargs keep prior values (insert defaults apply).
        Pass explicit None to clear a nullable column.
        """
        existing = self.get_device(gateway_id, id)
        ts = _now()
        if existing is None:
            row = {
                "gateway_id": gateway_id,
                "id": id,
                "ip": None if ip is _UNSET else ip,
                "topics_rw": (
                    None if topics_rw is _UNSET else _json_text(topics_rw)
                ),
                "topics_r": (
                    None if topics_r is _UNSET else _json_text(topics_r)
                ),
                "monitor_enabled": (
                    1 if monitor_enabled is _UNSET else int(monitor_enabled)
                ),
                "cert_expires_at": (
                    None if cert_expires_at is _UNSET else cert_expires_at
                ),
                "cert_fingerprint": (
                    None if cert_fingerprint is _UNSET else cert_fingerprint
                ),
                "created_at": created_at if created_at is not None else ts,
                "meta_json": (
                    None if meta_json is _UNSET else _json_text(meta_json)
                ),
            }
        else:
            row = {
                "gateway_id": gateway_id,
                "id": id,
                "ip": existing["ip"] if ip is _UNSET else ip,
                "topics_rw": (
                    existing["topics_rw"]
                    if topics_rw is _UNSET
                    else _json_text(topics_rw)
                ),
                "topics_r": (
                    existing["topics_r"]
                    if topics_r is _UNSET
                    else _json_text(topics_r)
                ),
                "monitor_enabled": (
                    existing["monitor_enabled"]
                    if monitor_enabled is _UNSET
                    else int(monitor_enabled)
                ),
                "cert_expires_at": (
                    existing["cert_expires_at"]
                    if cert_expires_at is _UNSET
                    else cert_expires_at
                ),
                "cert_fingerprint": (
                    existing["cert_fingerprint"]
                    if cert_fingerprint is _UNSET
                    else cert_fingerprint
                ),
                "created_at": existing["created_at"],
                "meta_json": (
                    existing["meta_json"]
                    if meta_json is _UNSET
                    else _json_text(meta_json)
                ),
            }
        self._conn.execute(
            """
            INSERT INTO devices (
              gateway_id, id, ip, topics_rw, topics_r, monitor_enabled,
              cert_expires_at, cert_fingerprint, created_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gateway_id, id) DO UPDATE SET
              ip = excluded.ip,
              topics_rw = excluded.topics_rw,
              topics_r = excluded.topics_r,
              monitor_enabled = excluded.monitor_enabled,
              cert_expires_at = excluded.cert_expires_at,
              cert_fingerprint = excluded.cert_fingerprint,
              meta_json = excluded.meta_json
            """,
            (
                row["gateway_id"],
                row["id"],
                row["ip"],
                row["topics_rw"],
                row["topics_r"],
                row["monitor_enabled"],
                row["cert_expires_at"],
                row["cert_fingerprint"],
                row["created_at"],
                row["meta_json"],
            ),
        )
        self._conn.commit()
        out = self.get_device(gateway_id, id)
        assert out is not None
        return out

    @_locked
    def get_device(self, gateway_id: str, device_id: str) -> Optional[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM devices WHERE gateway_id = ? AND id = ?",
            (gateway_id, device_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @_locked
    def list_devices(
        self,
        gateway_id: str,
        monitor_enabled: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        if monitor_enabled is None:
            cur = self._conn.execute(
                "SELECT * FROM devices WHERE gateway_id = ? ORDER BY id",
                (gateway_id,),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT * FROM devices
                WHERE gateway_id = ? AND monitor_enabled = ?
                ORDER BY id
                """,
                (gateway_id, int(monitor_enabled)),
            )
        return [dict(r) for r in cur.fetchall()]

    @_locked
    def delete_device(self, gateway_id: str, device_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM devices WHERE gateway_id = ? AND id = ?",
            (gateway_id, device_id),
        )
        self._conn.execute(
            """
            UPDATE imported_allowlist_ips
            SET linked_device_id = NULL
            WHERE gateway_id = ? AND linked_device_id = ?
            """,
            (gateway_id, device_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    @_locked
    def delete_gateway(self, gateway_id: str) -> bool:
        """Remove a gateway and its devices / allow-list rows.

        Audit rows are kept (gateway_id nullable historically). Returns False
        when the gateway id was unknown.
        """
        existing = self.get_gateway(gateway_id)
        if existing is None:
            return False
        self._conn.execute(
            "DELETE FROM devices WHERE gateway_id = ?", (gateway_id,)
        )
        self._conn.execute(
            "DELETE FROM imported_allowlist_ips WHERE gateway_id = ?",
            (gateway_id,),
        )
        cur = self._conn.execute(
            "DELETE FROM gateways WHERE id = ?", (gateway_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── allow-list import / link ──────────────────────────────────────

    @_locked
    def import_allowlist_ips(
        self,
        gateway_id: str,
        ips: Iterable[str],
    ) -> int:
        """Import allow-list IPs. Idempotent; does not overwrite existing links."""
        inserted = 0
        for ip in ips:
            ip = (ip or "").strip()
            if not ip:
                continue
            cur = self._conn.execute(
                """
                INSERT INTO imported_allowlist_ips (gateway_id, ip, linked_device_id)
                VALUES (?, ?, NULL)
                ON CONFLICT(gateway_id, ip) DO NOTHING
                """,
                (gateway_id, ip),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    @_locked
    def link_allowlist_ip(
        self,
        gateway_id: str,
        ip: str,
        device_id: str,
    ) -> dict[str, Any]:
        """Link an imported allow-list IP to a device id (UI step)."""
        if self.get_device(gateway_id, device_id) is None:
            raise ValueError(
                f"device {device_id!r} not found on gateway {gateway_id!r}"
            )
        self._conn.execute(
            """
            INSERT INTO imported_allowlist_ips (gateway_id, ip, linked_device_id)
            VALUES (?, ?, ?)
            ON CONFLICT(gateway_id, ip) DO UPDATE SET
              linked_device_id = excluded.linked_device_id
            """,
            (gateway_id, ip, device_id),
        )
        self._conn.commit()
        cur = self._conn.execute(
            """
            SELECT * FROM imported_allowlist_ips
            WHERE gateway_id = ? AND ip = ?
            """,
            (gateway_id, ip),
        )
        row = cur.fetchone()
        assert row is not None
        return dict(row)

    @_locked
    def list_allowlist_ips(self, gateway_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT * FROM imported_allowlist_ips
            WHERE gateway_id = ?
            ORDER BY ip
            """,
            (gateway_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    @_locked
    def import_acl_usernames(
        self,
        gateway_id: str,
        usernames: Iterable[str],
        *,
        topics_rw: Any = None,
        topics_r: Any = None,
    ) -> int:
        """Import ACL usernames as devices with ip=NULL when missing.

        Existing devices are left unchanged (brownfield: do not invent joins).
        """
        created = 0
        ts = _now()
        rw = _json_text(topics_rw)
        r = _json_text(topics_r)
        for username in usernames:
            username = (username or "").strip()
            if not username:
                continue
            cur = self._conn.execute(
                """
                INSERT INTO devices (
                  gateway_id, id, ip, topics_rw, topics_r, monitor_enabled,
                  created_at
                ) VALUES (?, ?, NULL, ?, ?, 1, ?)
                ON CONFLICT(gateway_id, id) DO NOTHING
                """,
                (gateway_id, username, rw, r, ts),
            )
            created += cur.rowcount
        self._conn.commit()
        return created

    # ── audit ─────────────────────────────────────────────────────────

    @_locked
    def audit(
        self,
        action: str,
        *,
        gateway_id: Optional[str] = None,
        device_id: Optional[str] = None,
        detail: Any = None,
        actor: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> dict[str, Any]:
        """Append an audit_log row. detail may be str or JSON-serializable."""
        ts = timestamp if timestamp is not None else _now()
        detail_json = detail
        if detail_json is not None and not isinstance(detail_json, str):
            detail_json = json.dumps(detail_json, ensure_ascii=False)
        cur = self._conn.execute(
            """
            INSERT INTO audit_log (
              ts, action, gateway_id, device_id, detail_json, actor
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, action, gateway_id, device_id, detail_json, actor),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        cur = self._conn.execute(
            "SELECT * FROM audit_log WHERE id = ?", (row_id,)
        )
        row = cur.fetchone()
        assert row is not None
        return dict(row)

    @_locked
    def list_audit(
        self,
        *,
        gateway_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if gateway_id is None:
            cur = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY ts DESC, id DESC LIMIT ?",
                (int(limit),),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT * FROM audit_log
                WHERE gateway_id = ?
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (gateway_id, int(limit)),
            )
        return [dict(r) for r in cur.fetchall()]
