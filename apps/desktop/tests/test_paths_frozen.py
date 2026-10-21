"""Path helpers for source vs frozen (PyInstaller) layouts."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from shell import paths


class PathsTests(unittest.TestCase):
    def test_repo_root_respects_env(self) -> None:
        with mock.patch.dict(os.environ, {"IOTGW_REPO_ROOT": "/tmp/iotgw-root"}):
            self.assertEqual(paths.repo_root(), Path("/tmp/iotgw-root").resolve())

    def test_desktop_ui_dir_source(self) -> None:
        ui = paths.desktop_ui_dir()
        self.assertTrue(ui.is_dir(), msg=ui)
        self.assertTrue((ui / "wizard.html").is_file())
        self.assertTrue((ui / "app-shell.js").is_file())
        self.assertTrue((ui / "splash.html").is_file())
        self.assertTrue((ui / "settings.html").is_file())
        self.assertTrue((ui / "settings.js").is_file())
        self.assertTrue((ui / "security.html").is_file())
        self.assertTrue((ui / "security.js").is_file())
        self.assertTrue((ui / "gateways.html").is_file())
        self.assertTrue((ui / "gateways.js").is_file())

    def test_control_service_dir_source(self) -> None:
        cs = paths.control_service_dir()
        self.assertTrue((cs / "api" / "__main__.py").is_file())

    def test_frozen_layout(self) -> None:
        fake = Path("/tmp/fake-meipass")
        with (
            mock.patch.object(paths.sys, "frozen", True, create=True),
            mock.patch.object(paths.sys, "_MEIPASS", str(fake), create=True),
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("IOTGW_REPO_ROOT", None)
            self.assertTrue(paths.is_frozen())
            self.assertEqual(paths.bundle_root(), fake)
            self.assertEqual(paths.repo_root(), fake)
            self.assertEqual(paths.desktop_ui_dir(), fake / "ui")
            self.assertEqual(paths.control_service_dir(), fake / "control-service")


if __name__ == "__main__":
    unittest.main()
