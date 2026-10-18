"""Windows-safe process terminate (no os.killpg)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from shell import launcher


class TerminateTests(unittest.TestCase):
    def test_terminate_uses_proc_api_on_windows(self) -> None:
        proc = mock.Mock()
        proc.poll.side_effect = [None, None, 0]
        proc.terminate = mock.Mock()
        proc.kill = mock.Mock()

        with mock.patch.object(launcher.os, "name", "nt"):
            with mock.patch.object(launcher.time, "sleep", return_value=None):
                with mock.patch.object(launcher.os, "killpg", create=True) as killpg:
                    launcher._terminate(proc, grace=0.2)
        proc.terminate.assert_called()
        killpg.assert_not_called()

    def test_terminate_uses_killpg_on_posix(self) -> None:
        proc = mock.Mock()
        proc.pid = 4242
        proc.poll.side_effect = [None, 0]
        proc.terminate = mock.Mock()

        fake_killpg = mock.Mock()
        with mock.patch.object(launcher.os, "name", "posix"):
            with mock.patch.object(launcher.os, "killpg", fake_killpg, create=True):
                with mock.patch.object(launcher.time, "sleep", return_value=None):
                    # Ensure hasattr(os, "killpg") is True under the patch.
                    launcher._terminate(proc, grace=0.2)
        fake_killpg.assert_called()
        proc.terminate.assert_not_called()

    def test_terminate_noop_when_already_exited(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = 0
        launcher._terminate(proc, grace=0.1)
        proc.terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
