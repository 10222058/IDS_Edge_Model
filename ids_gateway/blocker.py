from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from typing import Dict


class IptablesBlocker:
    def __init__(self, block_seconds: int) -> None:
        self.block_seconds = block_seconds
        self.blocked_until: Dict[str, datetime] = {}

    def block_ip(self, ip: str) -> None:
        now = datetime.now()
        if ip in self.blocked_until and self.blocked_until[ip] > now:
            return

        cmd = ["sudo", "iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"]
        subprocess.run(cmd, check=False)
        self.blocked_until[ip] = now + timedelta(seconds=self.block_seconds)

    def unblock_expired(self) -> None:
        now = datetime.now()
        expired_ips = [ip for ip, until in self.blocked_until.items() if until <= now]
        for ip in expired_ips:
            cmd = ["sudo", "iptables", "-D", "FORWARD", "-s", ip, "-j", "DROP"]
            subprocess.run(cmd, check=False)
            del self.blocked_until[ip]
