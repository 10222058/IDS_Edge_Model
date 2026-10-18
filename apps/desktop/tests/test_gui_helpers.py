"""GUI launcher helpers (no window open)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shell import gui


class GuiHelperTests(unittest.TestCase):
    def test_with_local_libs_noop_when_missing(self) -> None:
        env = gui._with_local_libs({"FOO": "1"})
        self.assertEqual(env["FOO"], "1")

    def test_playwright_chrome_optional(self) -> None:
        # May be None in CI without playwright browsers — must not raise.
        path = gui._playwright_chrome()
        if path is not None:
            self.assertTrue(path.is_file())

    def test_browser_using_profile_reads_windows_lockfile(self) -> None:
        profile = Path(tempfile.mkdtemp())
        self.assertFalse(gui._browser_using_profile(profile))
        (profile / "lockfile").write_text("", encoding="utf-8")
        self.assertTrue(gui._browser_using_profile(profile))

    def test_browser_using_profile_reads_singleton_lock(self) -> None:
        profile = Path(tempfile.mkdtemp())
        self.assertFalse(gui._browser_using_profile(profile))
        (profile / "SingletonLock").write_text("lock", encoding="utf-8")
        self.assertTrue(gui._browser_using_profile(profile))

    def test_wait_returns_after_profile_browser_exits(self) -> None:
        profile = Path(tempfile.mkdtemp())
        launcher = mock.Mock()
        # Broker already exited (Chromium on Windows).
        launcher.poll.return_value = 0
        # First polls: browser holds profile; then it is gone.
        with mock.patch(
            "shell.gui._browser_using_profile", side_effect=[True, True, False]
        ):
            gui._wait_while_app_window_open(
                launcher, profile, startup_grace_s=0.01, poll_s=0.001
            )

    def test_wait_gives_up_when_broker_exits_without_window(self) -> None:
        profile = Path(tempfile.mkdtemp())
        launcher = mock.Mock()
        launcher.poll.return_value = 1
        with mock.patch("shell.gui._browser_using_profile", return_value=False):
            gui._wait_while_app_window_open(
                launcher, profile, startup_grace_s=0.01, poll_s=0.001
            )


if __name__ == "__main__":
    unittest.main()
