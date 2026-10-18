"""Unit tests for Docker/WSL detection helpers."""

from __future__ import annotations

import unittest
from unittest import mock

from shell import detect


class DetectDockerTests(unittest.TestCase):
    def test_missing_docker(self) -> None:
        with mock.patch("shell.detect.shutil.which", return_value=None):
            st = detect.detect_docker()
        self.assertFalse(st.present)
        self.assertFalse(st.healthy)
        self.assertIn("not required", st.hint.lower())

    def test_healthy_docker(self) -> None:
        ver = mock.Mock(returncode=0, stdout="27.0.0\n", stderr="")
        info = mock.Mock(returncode=0, stdout="Server:\n", stderr="")

        def run(args, **kwargs):
            if args[1] == "version":
                return ver
            return info

        with (
            mock.patch("shell.detect.shutil.which", return_value="/usr/bin/docker"),
            mock.patch("shell.detect.subprocess.run", side_effect=run),
        ):
            st = detect.detect_docker()
        self.assertTrue(st.present)
        self.assertTrue(st.healthy)
        self.assertEqual(st.version, "27.0.0")

    def test_daemon_down(self) -> None:
        ver = mock.Mock(returncode=1, stdout="", stderr="")
        info = mock.Mock(returncode=1, stdout="", stderr="Cannot connect\n")

        def run(args, **kwargs):
            if args[1] == "version":
                return ver
            return info

        with (
            mock.patch("shell.detect.shutil.which", return_value="/usr/bin/docker"),
            mock.patch("shell.detect.subprocess.run", side_effect=run),
        ):
            st = detect.detect_docker()
        self.assertTrue(st.present)
        self.assertFalse(st.healthy)


class DetectWslTests(unittest.TestCase):
    def test_snapshot_keys(self) -> None:
        snap = detect.environment_snapshot()
        self.assertIn("docker", snap)
        self.assertIn("wsl", snap)
        self.assertIn("platform", snap)
        self.assertIn("runtime", snap)
        self.assertFalse(snap["docker_required"])
        self.assertFalse(snap["wsl_required"])

    def test_operator_runtime_values(self) -> None:
        self.assertIn(detect.operator_runtime(), ("windows", "wsl", "linux"))


if __name__ == "__main__":
    unittest.main()
