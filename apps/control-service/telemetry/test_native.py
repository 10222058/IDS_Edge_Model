"""Unit tests for native Loki/Prometheus lifecycle helpers."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telemetry import native as nv
from telemetry.lifecycle import BackendStatus
from telemetry.readiness import ProbeResult


class NativeHelperTests(unittest.TestCase):
    def test_bind_conflict_message(self) -> None:
        msg = nv._bind_conflict_message(
            "level=error msg=\"listen tcp :3100: bind: address already in use\"",
            port=3100,
            name="Loki",
        )
        self.assertIsNotNone(msg)
        self.assertIn("3100", msg or "")
        self.assertIsNone(
            nv._bind_conflict_message("level=info msg=ready", port=3100, name="Loki")
        )

    def test_port_conflict_detail_when_occupied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = nv.NativeTelemetryManager(tmp)
            with mock.patch.object(nv, "_port_open", side_effect=[True, False]):
                with mock.patch.object(
                    nv,
                    "check_loki",
                    return_value=ProbeResult(False, "u", "down"),
                ):
                    with mock.patch.object(
                        nv,
                        "check_prometheus",
                        return_value=ProbeResult(True, "u", "ok"),
                    ):
                        detail = mgr._port_conflict_detail()
            self.assertIsNotNone(detail)
            self.assertIn("3100", detail or "")

    def test_start_fails_fast_on_port_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "bin" / "loki").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "bin" / "prometheus").write_text("#!/bin/sh\n", encoding="utf-8")
            mgr = nv.NativeTelemetryManager(root)
            not_ready = ProbeResult(False, "u", "down")
            with mock.patch.object(
                mgr, "_http_ready", return_value=(False, not_ready, not_ready)
            ):
                with mock.patch.object(
                    mgr,
                    "_port_conflict_detail",
                    return_value="Port 3100 is in use but Loki is not ready.",
                ):
                    st = mgr.start(ensure_bins=False, wait=False)
            self.assertFalse(st.running)
            self.assertIn("3100", st.detail)

    def test_start_reuses_already_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = nv.NativeTelemetryManager(tmp)
            ok = ProbeResult(True, "u", "ok")
            with mock.patch.object(mgr, "_http_ready", return_value=(True, ok, ok)):
                st = mgr.start(ensure_bins=False, wait=False)
            self.assertTrue(st.running)
            self.assertIn("running", st.detail.lower())


class PortOpenTests(unittest.TestCase):
    def test_port_open_false_when_free(self) -> None:
        # Bind ephemeral to learn a free port, then close and probe it.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertFalse(nv._port_open("127.0.0.1", port, timeout=0.2))

    def test_port_open_true_when_listening(self) -> None:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            self.assertTrue(nv._port_open("127.0.0.1", port, timeout=0.5))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
