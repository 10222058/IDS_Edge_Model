from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from scapy.all import IP, TCP, UDP, ICMP, sniff  # type: ignore


@dataclass
class PacketRecord:
    ts: float
    src_ip: str
    dst_ip: str
    dst_port: int
    proto: str
    pkt_len: int
    tcp_flags: str


class PacketCapture:
    def __init__(self, interface: str) -> None:
        self.interface = interface
        self.buffer: Dict[str, List[PacketRecord]] = defaultdict(list)

    def _parse_packet(self, pkt: Any) -> PacketRecord | None:
        if IP not in pkt:
            return None

        ip = pkt[IP]
        src_ip = str(ip.src)
        dst_ip = str(ip.dst)
        pkt_len = int(len(pkt))
        now = datetime.now().timestamp()

        dst_port = 0
        proto = "OTHER"
        flags = ""

        if TCP in pkt:
            proto = "TCP"
            dst_port = int(pkt[TCP].dport)
            flags = str(pkt[TCP].flags)
        elif UDP in pkt:
            proto = "UDP"
            dst_port = int(pkt[UDP].dport)
        elif ICMP in pkt:
            proto = "ICMP"

        return PacketRecord(
            ts=now,
            src_ip=src_ip,
            dst_ip=dst_ip,
            dst_port=dst_port,
            proto=proto,
            pkt_len=pkt_len,
            tcp_flags=flags,
        )

    def _on_packet(self, pkt: Any) -> None:
        rec = self._parse_packet(pkt)
        if rec is None:
            return
        self.buffer[rec.src_ip].append(rec)

    def sniff_once(self, timeout: int = 2) -> Dict[str, List[PacketRecord]]:
        sniff(
            iface=self.interface,
            prn=self._on_packet,
            store=False,
            timeout=timeout,
        )
        data = dict(self.buffer)
        self.buffer.clear()
        return data
